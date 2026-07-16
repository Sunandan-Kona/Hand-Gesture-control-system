import os
import sys
import warnings
import json

# Suppress all compiler and C++ library logging (TensorFlow / MediaPipe / OpenCV)
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['GLOG_minloglevel'] = '2'
warnings.filterwarnings("ignore", category=UserWarning)

# Redirect standard error (stderr) to devnull permanently to silence low-level DLL warnings.
# C++ logs from TF Lite/MediaPipe are sent to stderr; this discards them completely.
try:
    stderr_fd = sys.stderr.fileno()
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stderr_fd)
except Exception:
    pass

import cv2  # noqa: E402
import mediapipe as mp  # noqa: E402
import pyautogui  # noqa: E402
import math  # noqa: E402
import keyboard  # noqa: E402
import time  # noqa: E402
import webbrowser  # noqa: E402
from flask import Flask, render_template, Response  # noqa: E402
from flask_socketio import SocketIO  # noqa: E402
import threading  # noqa: E402

# Optimize PyAutoGUI settings for responsiveness
pyautogui.FAILSAFE = False  # Disabled to prevent crash when hand first appears or goes out of bounds
pyautogui.PAUSE = 0.001  # Minimum delay to keep cursor movement smooth

# Initialize MediaPipe Hands (Configure to track 2 hands at once)
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,  # Support dual-hand simultaneous control
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7
)

# Screen Dimensions
screen_w, screen_h = pyautogui.size()

# Active camera tracking box (normalized coordinates)
X_MIN, X_MAX = 0.15, 0.85
Y_MIN, Y_MAX = 0.20, 0.80

# Cursor Smoothing (Adaptive Exponential Moving Average)
prev_screen_x, prev_screen_y = pyautogui.position()

# Raw Index Finger anchors for Relative Trackpad mode
prev_raw_x = None
prev_raw_y = None
trackpad_mode = False

# Gesture Thresholds (relative to Hand Reference Distance)
PINCH_RATIO_THRESHOLD = 0.22

# State Variables to prevent repeated triggering
is_dragging = False
middle_pinched = False
ring_pinched = False
pinky_pinched = False
is_paused = False
fist_active = False
open_hand_active = False
was_minimized = False
finger_heart_start_time = None
running = True

# Smoothing constants
min_alpha = 0.08
max_alpha = 0.70

# Action Toast Notification Variables
action_toast_text = ""
action_toast_time = 0.0
action_toast_color = (0, 255, 255)

# Tutorial Mode Variables
tutorial_active = True
tutorial_step = 0
tutorial_instructions = [
    "Move Index Finger to Center Target",
    "Pinch Index + Thumb (Left Click)",
    "Double Pinch Index + Thumb (Double Click)",
    "Pinch Middle + Thumb (Right Click)",
    "Raise Index + Middle (Scroll Mode)",
    "Raise Thumb + Pinky (Volume Mode)",
    "Pinch Ring + Thumb (Copy Action)",
    "Pinch Pinky + Thumb (Paste Action)"
]

# Frame Buffer for browser MJPEG streaming
latest_frame = None
frame_lock = threading.Lock()

# Persistent configuration filename
CONFIG_FILE = "hand_config.json"

# Default configuration settings dictionary
config = {
    "pinch_threshold": 0.22,
    "smoothing_min": 0.08,
    "smoothing_max": 0.70,
    "margin_x": 0.15,
    "margin_y": 0.20,
    "trackpad_mode": False,
    "hotkeys": {
        "minimize": ["win", "down"],
        "maximize": ["win", "up"],
        "copy": ["ctrl", "c"],
        "paste": ["ctrl", "v"]
    },
    "open_hand_ratio": 0.78,
    "fist_ratio": 0.54
}

def load_config_file():
    """Load configuration from local hand_config.json file into global parameters."""
    global config, PINCH_RATIO_THRESHOLD, X_MIN, X_MAX, Y_MIN, Y_MAX, min_alpha, max_alpha, trackpad_mode
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config.update(json.load(f))
        except Exception as e:
            print(f"[SYSTEM] Error reading configuration: {e}")
    else:
        save_config_file()

    PINCH_RATIO_THRESHOLD = config.get("pinch_threshold", 0.22)
    min_alpha = config.get("smoothing_min", 0.08)
    max_alpha = config.get("smoothing_max", 0.70)
    
    mx = config.get("margin_x", 0.15)
    my = config.get("margin_y", 0.20)
    X_MIN, X_MAX = mx, 1.0 - mx
    Y_MIN, Y_MAX = my, 1.0 - my
    
    trackpad_mode = config.get("trackpad_mode", False)

def save_config_file():
    """Save active configuration to local hand_config.json file."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        socketio.emit('load_config', config)
    except Exception as e:
        print(f"[SYSTEM] Error saving configuration: {e}")

# Temporary list metrics for calibration step captures
calibration_capture_gesture = None
temp_open_ratios = []
temp_fist_ratios = []
temp_pinch_ratios = []

# Initialize Flask Web Server
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = 'antigravity_secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

@app.route('/')
def index():
    return render_template('index.html')

def gen_frames():
    """Generate JPEG frame stream for the browser's MJPEG player."""
    global latest_frame
    while True:
        with frame_lock:
            if latest_frame is None:
                time.sleep(0.03)
                continue
            frame_bytes = latest_frame
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.03)

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@socketio.on('connect')
def handle_connect():
    socketio.emit('load_config', config)

@socketio.on('toggle_pause')
def handle_toggle_pause():
    global is_paused
    is_paused = not is_paused
    socketio.emit('status_update', {'is_paused': is_paused})
    print(f"[SYSTEM] Pause state toggled from Web Dashboard: {is_paused}")

@socketio.on('skip_tutorial')
def handle_skip_tutorial():
    global tutorial_active, tutorial_step
    tutorial_active = False
    socketio.emit('status_update', {'tutorial_active': False})
    trigger_toast("SYSTEM UNLOCKED", (0, 255, 0))
    print("[SYSTEM] Tutorial skipped from browser dashboard.")

@socketio.on('save_hotkeys')
def handle_save_hotkeys(data):
    global config
    config["hotkeys"] = data
    save_config_file()
    print("[SYSTEM] Custom hotkey configuration updated.")

@socketio.on('calibration_capture')
def handle_calibration_capture(data):
    global calibration_capture_gesture
    gesture = data.get('gesture')
    calibration_capture_gesture = gesture
    print(f"[SYSTEM] Calibration capture requested for gesture: {gesture}")

@socketio.on('calibration_cancel')
def handle_calibration_cancel():
    global calibration_capture_gesture, temp_open_ratios, temp_fist_ratios, temp_pinch_ratios
    calibration_capture_gesture = None
    temp_open_ratios = []
    temp_fist_ratios = []
    temp_pinch_ratios = []
    print("[SYSTEM] Calibration Wizard cancelled.")

@socketio.on('calibration_save')
def handle_calibration_save():
    global temp_open_ratios, temp_fist_ratios, temp_pinch_ratios, config
    
    if temp_open_ratios:
        avg_open = sum(temp_open_ratios) / len(temp_open_ratios)
        config["open_hand_ratio"] = round(avg_open - 0.05, 3)
    
    if temp_fist_ratios:
        avg_fist = sum(temp_fist_ratios) / len(temp_fist_ratios)
        config["fist_ratio"] = round(avg_fist + 0.05, 3)
        
    if temp_pinch_ratios:
        avg_pinch = sum(temp_pinch_ratios) / len(temp_pinch_ratios)
        config["pinch_threshold"] = round(avg_pinch + 0.04, 3)
        
    # Reset temp arrays
    temp_open_ratios = []
    temp_fist_ratios = []
    temp_pinch_ratios = []
    
    save_config_file()
    load_config_file()
    print("[SYSTEM] Calibration wizard completed and configuration saved.")

@socketio.on('setting_change')
def handle_setting_change(data):
    global config
    setting = data.get('setting')
    val = data.get('value')
    
    if setting == 'trackpad_mode':
        config['trackpad_mode'] = val
    elif setting == 'pinch_threshold':
        config['pinch_threshold'] = val
    elif setting == 'smoothing_min':
        config['smoothing_min'] = val
    elif setting == 'smoothing_max':
        config['smoothing_max'] = val
    elif setting == 'margin_x':
        config['margin_x'] = val
    elif setting == 'margin_y':
        config['margin_y'] = val
        
    save_config_file()
    load_config_file()
    print(f"[SYSTEM] Config adjusted: {setting} = {val}")

def get_distance(p1, p2):
    """Calculate Euclidean distance between two points."""
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

def trigger_toast(text, color=(0, 255, 255)):
    """Set the parameters for the on-screen glowing toast notification."""
    global action_toast_text, action_toast_time, action_toast_color
    action_toast_text = text
    action_toast_time = time.time()
    action_toast_color = color
    try:
        socketio.emit('status_update', {'actions': [text]})
    except Exception:
        pass

def main():
    global prev_screen_x, prev_screen_y, prev_raw_x, prev_raw_y
    global is_dragging, middle_pinched, ring_pinched, pinky_pinched, is_paused
    global tutorial_active, tutorial_step
    global fist_active, open_hand_active, was_minimized, finger_heart_start_time
    global X_MIN, X_MAX, Y_MIN, Y_MAX, PINCH_RATIO_THRESHOLD, min_alpha, max_alpha, trackpad_mode
    global latest_frame, running

    # Load configuration
    load_config_file()

    # Persistent Gesture State Variables
    scroll_anchor_y = None
    vol_anchor_y = None
    last_vol_change_time = 0.0
    last_pinch_release_time = 0.0
    last_scroll_time = 0.0
    is_double_clicked_state = False
    
    # Click Freeze timing
    last_click_down_time = 0.0
    
    # Static Gesture Frame verification counters
    thumbs_up_frames = 0
    spiderman_frames = 0

    def toggle_pause():
        global is_paused
        is_paused = not is_paused
        socketio.emit('status_update', {'is_paused': is_paused})
        print(f"\n[SYSTEM] Gesture Control {'PAUSED' if is_paused else 'RESUMED'}")

    keyboard.add_hotkey('f8', toggle_pause)

    # Launch Flask Server in a daemon background thread
    def run_server():
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        socketio.run(app, host='0.0.0.0', port=5000, log_output=False, use_reloader=False)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    print("\n" + "="*50)
    print("      ANTIGRAVITY DUAL HAND CONTROL DASHBOARD SERVER RUNNING")
    print("      Dashboard URL: http://localhost:5000")
    print("="*50)

    # Automatically open the web browser dashboard on port 5000
    time.sleep(0.5)
    webbrowser.open("http://localhost:5000")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open webcam.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    fps_start_time = time.time()
    fps_counter = 0
    fps = 30
    frame_idx = 0

    running = True
    while cap.isOpened() and running:
        success, frame = cap.read()
        if not success:
            time.sleep(0.1)
            continue

        frame_idx += 1
        fps_counter += 1
        if time.time() - fps_start_time > 1.0:
            fps = fps_counter
            fps_counter = 0
            fps_start_time = time.time()

        if frame_idx % 10 == 0:
            try:
                socketio.emit('status_update', {
                    'is_paused': is_paused,
                    'tutorial_step': tutorial_step,
                    'tutorial_active': tutorial_active,
                    'fps': fps
                })
            except Exception:
                pass

        frame = cv2.flip(frame, 1)
        h, w, _ = frame.shape

        image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands.process(image_rgb)

        status_text = "No Hand Detected"
        status_color = (0, 0, 255)
        hud_actions = []

        if is_paused:
            status_text = "PAUSED (F8 to Resume)"
            status_color = (128, 128, 128)

        if results.multi_hand_landmarks and not is_paused:
            status_text = "Iron Man Mode Active"
            status_color = (0, 255, 0)

            # Separate Hand Roles
            num_hands = len(results.multi_hand_landmarks)

            for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
                # Retrieve Left/Right hand labels. 
                # Note: Flipped mapping because camera image is mirrored. 
                # "Left" classification = physical RIGHT hand. 
                # "Right" classification = physical LEFT hand.
                label = results.multi_handedness[idx].classification[0].label
                is_right_hand = (label == "Left")
                is_left_hand = (label == "Right")

                if num_hands == 1:
                    # Single Hand: controls all features
                    allow_mouse = True
                    allow_shortcuts = True
                else:
                    # Dual Hands: Right Hand = Navigation/Clicks, Left Hand = Scrolls/Volume/Gestures
                    allow_mouse = is_right_hand
                    allow_shortcuts = is_left_hand

                try:
                    # Custom skeletal color overlay depending on Left/Right classification
                    # Cyan (255, 255, 0) for Right hand, Magenta (255, 0, 255) for Left hand
                    color = (255, 255, 0) if is_right_hand else (255, 0, 255)
                    mp_drawing.draw_landmarks(
                        frame,
                        hand_landmarks,
                        mp_hands.HAND_CONNECTIONS,
                        mp_drawing.DrawingSpec(color=color, thickness=2, circle_radius=2),
                        mp_drawing.DrawingSpec(color=color, thickness=2)
                    )

                    def get_pt(lm_id):
                        lm = hand_landmarks.landmark[lm_id]
                        return int(lm.x * w), int(lm.y * h)

                    wrist = get_pt(mp_hands.HandLandmark.WRIST)
                    index_mcp = get_pt(mp_hands.HandLandmark.INDEX_FINGER_MCP)
                    middle_mcp = get_pt(mp_hands.HandLandmark.MIDDLE_FINGER_MCP)
                    ring_mcp = get_pt(mp_hands.HandLandmark.RING_FINGER_MCP)
                    pinky_mcp = get_pt(mp_hands.HandLandmark.PINKY_MCP)
                    thumb_mcp = get_pt(mp_hands.HandLandmark.THUMB_MCP)
                    
                    thumb_tip = get_pt(mp_hands.HandLandmark.THUMB_TIP)
                    index_tip = get_pt(mp_hands.HandLandmark.INDEX_FINGER_TIP)
                    middle_tip = get_pt(mp_hands.HandLandmark.MIDDLE_FINGER_TIP)
                    ring_tip = get_pt(mp_hands.HandLandmark.RING_FINGER_TIP)
                    pinky_tip = get_pt(mp_hands.HandLandmark.PINKY_TIP)

                    ref_dist = get_distance(wrist, index_mcp)
                    if ref_dist == 0:
                        ref_dist = 1

                    ratio_index = get_distance(thumb_tip, index_tip) / ref_dist
                    ratio_middle = get_distance(thumb_tip, middle_tip) / ref_dist
                    ratio_ring = get_distance(thumb_tip, ring_tip) / ref_dist
                    ratio_pinky = get_distance(thumb_tip, pinky_tip) / ref_dist

                    # 3D Depth Check to prevent false sideways triggers:
                    # Normalized depth coordinate (z) distance between thumb and fingers must be < 0.08
                    z_diff_index = abs(hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].z - hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP].z)
                    z_diff_middle = abs(hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].z - hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_TIP].z)
                    z_diff_ring = abs(hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].z - hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_TIP].z)
                    z_diff_pinky = abs(hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].z - hand_landmarks.landmark[mp_hands.HandLandmark.PINKY_TIP].z)

                    pinched_index = (ratio_index < PINCH_RATIO_THRESHOLD) and (z_diff_index < 0.08)
                    pinched_middle = (ratio_middle < PINCH_RATIO_THRESHOLD) and (z_diff_middle < 0.08)
                    pinched_ring = (ratio_ring < PINCH_RATIO_THRESHOLD) and (z_diff_ring < 0.08)
                    pinched_pinky = (ratio_pinky < PINCH_RATIO_THRESHOLD) and (z_diff_pinky < 0.08)

                    idx_tip = hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP]
                    if pinched_index or is_dragging:
                        thb_tip = hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP]
                        lm_x, lm_y = (idx_tip.x + thb_tip.x) / 2.0, (idx_tip.y + thb_tip.y) / 2.0
                    else:
                        lm_x, lm_y = idx_tip.x, idx_tip.y

                    # Calculate movement smoothing factor
                    if pinched_index or is_dragging:
                        curr_min_alpha = min_alpha * 0.6
                        curr_max_alpha = max_alpha * 0.6
                    else:
                        curr_min_alpha = min_alpha
                        curr_max_alpha = max_alpha

                    ratio_index_fold = get_distance(index_tip, index_mcp) / ref_dist
                    ratio_middle_fold = get_distance(middle_tip, middle_mcp) / ref_dist
                    ratio_ring_fold = get_distance(ring_tip, ring_mcp) / ref_dist
                    ratio_pinky_fold = get_distance(pinky_tip, pinky_mcp) / ref_dist
                    ratio_thumb_ext = get_distance(thumb_tip, thumb_mcp) / ref_dist

                    # --- Auto-Calibration Capture Check ---
                    global calibration_capture_gesture, temp_open_ratios, temp_fist_ratios, temp_pinch_ratios
                    if calibration_capture_gesture:
                        finger_fold_ratios = [ratio_index_fold, ratio_middle_fold, ratio_ring_fold, ratio_pinky_fold]
                        if calibration_capture_gesture == "open":
                            temp_open_ratios.append(sum(finger_fold_ratios) / 4.0)
                        elif calibration_capture_gesture == "fist":
                            temp_fist_ratios.append(sum(finger_fold_ratios) / 4.0)
                        elif calibration_capture_gesture == "pinch":
                            temp_pinch_ratios.append(ratio_index)
                        calibration_capture_gesture = None # Consume capture request

                    # Define isolated, mutually exclusive Boolean gesture boundaries (Instant clicking)
                    is_pure_left_click = pinched_index and (not pinched_middle)
                    is_pure_right_click = pinched_middle and (not pinched_index)
                    is_pure_copy = pinched_ring and (not pinched_index) and (not pinched_middle)
                    is_pure_paste = pinched_pinky and (not pinched_index) and (not pinched_middle)

                    # --- Mouse Navigation Block (Right Hand / Single Hand) ---
                    if allow_mouse:
                        if trackpad_mode:
                            # --- RELATIVE TRACKPAD MODE ---
                            if prev_raw_x is None:
                                prev_raw_x = lm_x
                                prev_raw_y = lm_y
                                dx_pixels = 0
                                dy_pixels = 0
                            else:
                                dx = lm_x - prev_raw_x
                                dy = lm_y - prev_raw_y
                                prev_raw_x = lm_x
                                prev_raw_y = lm_y
                                dx_pixels = dx * screen_w
                                dy_pixels = dy * screen_h

                            dist = math.hypot(dx_pixels, dy_pixels)
                            if dist < 1.5:
                                dx_pixels = 0
                                dy_pixels = 0
                                multiplier = 0
                            else:
                                # Dynamic mouse acceleration
                                multiplier = 1.0 + (dist / 10.0)

                            smooth_dx = dx_pixels * multiplier * curr_max_alpha
                            smooth_dy = dy_pixels * multiplier * curr_max_alpha

                            # Click Freeze: Freeze cursor relative movement during click down (150ms)
                            if is_dragging and (time.time() - last_click_down_time < 0.15):
                                smooth_dx = 0
                                smooth_dy = 0

                            if abs(smooth_dx) > 0 or abs(smooth_dy) > 0:
                                pyautogui.moveRel(int(smooth_dx), int(smooth_dy))
                                prev_screen_x, prev_screen_y = pyautogui.position()
                        else:
                            # --- ABSOLUTE MODE ---
                            prev_raw_x = None
                            prev_raw_y = None

                            x_clamped = max(X_MIN, min(lm_x, X_MAX))
                            y_clamped = max(Y_MIN, min(lm_y, Y_MAX))

                            target_screen_x = ((x_clamped - X_MIN) / (X_MAX - X_MIN)) * screen_w
                            target_screen_y = ((y_clamped - Y_MIN) / (Y_MAX - Y_MIN)) * screen_h

                            move_dist = math.hypot(target_screen_x - prev_screen_x, target_screen_y - prev_screen_y)
                            min_dist, max_dist = 5.0, 100.0

                            if move_dist < min_dist:
                                current_alpha = curr_min_alpha
                            elif move_dist > max_dist:
                                current_alpha = curr_max_alpha
                            else:
                                current_alpha = curr_min_alpha + (curr_max_alpha - curr_min_alpha) * ((move_dist - min_dist) / (max_dist - min_dist))

                            # Click Freeze: Freeze cursor absolute movement during click down (150ms)
                            if is_dragging and (time.time() - last_click_down_time < 0.15):
                                smooth_x, smooth_y = prev_screen_x, prev_screen_y
                            else:
                                smooth_x = int(current_alpha * target_screen_x + (1.0 - current_alpha) * prev_screen_x)
                                smooth_y = int(current_alpha * target_screen_y + (1.0 - current_alpha) * prev_screen_y)

                            pyautogui.moveTo(smooth_x, smooth_y)
                            prev_screen_x, prev_screen_y = smooth_x, smooth_y

                        # --- Click, Drag & Copy/Paste Actions (Instant zero-latency selection triggers) ---
                        if is_pure_left_click:
                            if not is_dragging and not is_double_clicked_state:
                                now = time.time()
                                if now - last_pinch_release_time < 0.35:
                                    pyautogui.doubleClick()
                                    is_double_clicked_state = True
                                    trigger_toast("DOUBLE CLICK", (255, 255, 0))
                                    hud_actions.append("DOUBLE CLICK")
                                    print("[ACTION] Double Click")
                                else:
                                    pyautogui.mouseDown()
                                    is_dragging = True
                                    last_click_down_time = time.time()  # Record click down for click freeze timing
                                    trigger_toast("LEFT CLICK (DOWN)", (0, 255, 0))
                                    print("[ACTION] Mouse Down (Drag Start)")
                            status_text = "DRAGGING" if is_dragging else "DOUBLE CLICK"
                            status_color = (255, 255, 0)
                            cv2.line(frame, thumb_tip, index_tip, (255, 255, 0), 3)
                        else:
                            if is_dragging:
                                pyautogui.mouseUp()
                                is_dragging = False
                                last_pinch_release_time = time.time()
                                trigger_toast("LEFT CLICK (UP)", (0, 255, 0))
                                print("[ACTION] Mouse Up (Drag End)")
                            if is_double_clicked_state:
                                is_double_clicked_state = False
                                last_pinch_release_time = time.time()
                            cv2.line(frame, thumb_tip, index_tip, (0, 255, 0), 1)

                        # --- Right Click Action ---
                        if is_pure_right_click:
                            cv2.line(frame, thumb_tip, middle_tip, (0, 0, 255), 3)
                            if not middle_pinched:
                                pyautogui.rightClick()
                                middle_pinched = True
                                trigger_toast("RIGHT CLICK", (0, 0, 255))
                                hud_actions.append("RIGHT CLICK")
                                print("[ACTION] Right Click")
                        else:
                            middle_pinched = False
                            cv2.line(frame, thumb_tip, middle_tip, (0, 255, 0), 1)

                        # --- Copy Action (Ring finger) ---
                        if is_pure_copy:
                            cv2.line(frame, thumb_tip, ring_tip, (255, 0, 255), 3)
                            if not ring_pinched:
                                hk = config.get("hotkeys", {}).get("copy", ["ctrl", "c"])
                                pyautogui.hotkey(*hk)
                                ring_pinched = True
                                trigger_toast("COPY (Ctrl+C)", (255, 0, 255))
                                hud_actions.append("COPY")
                                print(f"[ACTION] Copy ({hk})")
                        else:
                            ring_pinched = False
                            cv2.line(frame, thumb_tip, ring_tip, (0, 255, 0), 1)

                        # --- Paste Action (Pinky finger) ---
                        if is_pure_paste:
                            cv2.line(frame, thumb_tip, pinky_tip, (0, 255, 255), 3)
                            if not pinky_pinched:
                                hk = config.get("hotkeys", {}).get("paste", ["ctrl", "v"])
                                pyautogui.hotkey(*hk)
                                pinky_pinched = True
                                trigger_toast("PASTE (Ctrl+V)", (255, 255, 0))
                                hud_actions.append("PASTE")
                                print(f"[ACTION] Paste ({hk})")
                        else:
                            pinky_pinched = False
                            cv2.line(frame, thumb_tip, pinky_tip, (0, 255, 0), 1)
                    else:
                        # Reset relative tracking anchor if right hand leaves the frame
                        prev_raw_x = None
                        prev_raw_y = None

                    # --- Shortcut Actions Block (Left Hand / Single Hand) ---
                    if allow_shortcuts:
                        # Check Scroll Up/Down Gestures: Index and Middle are fully extended, Ring and Pinky folded
                        index_extended = ratio_index_fold > 0.70
                        middle_extended = ratio_middle_fold > 0.70
                        index_middle_dist = get_distance(index_tip, middle_tip) / ref_dist
                        
                        is_scrolling_gesture = (index_extended and middle_extended and 
                                                (ratio_ring_fold < 0.60) and (ratio_pinky_fold < 0.60) and 
                                                (index_middle_dist < 0.50))

                        # Check Volume Gesture: Thumb and Pinky extended, Middle and Ring folded
                        thumb_extended = ratio_thumb_ext > 0.60
                        pinky_extended = ratio_pinky_fold > 0.70
                        is_volume_gesture = thumb_extended and pinky_extended and (ratio_middle_fold < 0.60) and (ratio_ring_fold < 0.60)

                        # --- Window Control Gestures (Thumbs-Up = Minimize, Spider-Man = Maximize) ---
                        fist_thresh = config.get("fist_ratio", 0.54)

                        # 1. Minimize: Thumbs-Up
                        thumb_tip_y_val = hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].y
                        index_tip_y_val = hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP].y
                        wrist_y_val = hand_landmarks.landmark[mp_hands.HandLandmark.WRIST].y
                        
                        thumb_is_up = (thumb_tip_y_val < wrist_y_val) and (thumb_tip_y_val < index_tip_y_val)
                        is_thumbs_up = thumb_is_up and (ratio_thumb_ext > 0.55) and (ratio_index_fold < fist_thresh) and (ratio_middle_fold < fist_thresh) and (ratio_ring_fold < fist_thresh) and (ratio_pinky_fold < fist_thresh)
                        
                        # 2. Maximize: Spider-Man
                        is_spiderman = (ratio_index_fold > 0.72) and (ratio_pinky_fold > 0.62) and (ratio_thumb_ext > 0.55) and (ratio_middle_fold < fist_thresh) and (ratio_ring_fold < fist_thresh)

                        # Gesture verification buffers (3 frames)
                        if is_thumbs_up:
                            thumbs_up_frames += 1
                        else:
                            thumbs_up_frames = 0

                        if is_spiderman:
                            spiderman_frames += 1
                        else:
                            spiderman_frames = 0

                        if thumbs_up_frames >= 3:
                            if not fist_active:
                                hk = config.get("hotkeys", {}).get("minimize", ["win", "down"])
                                pyautogui.hotkey(*hk)
                                fist_active = True
                                was_minimized = True
                                trigger_toast("MINIMIZE WINDOW", (0, 0, 255))
                                print(f"[ACTION] Minimize Window ({hk})")
                            continue
                        else:
                            if thumbs_up_frames == 0:
                                fist_active = False

                        if spiderman_frames >= 3:
                            if not open_hand_active:
                                if was_minimized:
                                    pyautogui.hotkey('alt', 'tab')
                                    time.sleep(0.20)
                                    hk = config.get("hotkeys", {}).get("maximize", ["win", "up"])
                                    pyautogui.hotkey(*hk)
                                    was_minimized = False
                                else:
                                    hk = config.get("hotkeys", {}).get("maximize", ["win", "up"])
                                    pyautogui.hotkey(*hk)
                                open_hand_active = True
                                trigger_toast("MAXIMIZE WINDOW", (0, 255, 0))
                                print(f"[ACTION] Maximize Window ({hk})")
                            continue
                        else:
                            if spiderman_frames == 0:
                                open_hand_active = False

                        # --- Exit Logic (Korean Finger Heart: Index and Thumb pinched, other fingers folded, hold for 1.5s) ---
                        index_tip_y_val = hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP].y
                        wrist_y_val = hand_landmarks.landmark[mp_hands.HandLandmark.WRIST].y
                        
                        is_finger_heart = (ratio_index < 0.32) and (index_tip_y_val < wrist_y_val) and (ratio_middle_fold < 0.55) and (ratio_ring_fold < 0.55) and (ratio_pinky_fold < 0.55)
                        
                        if is_finger_heart:
                            if finger_heart_start_time is None:
                                finger_heart_start_time = time.time()
                            elapsed = time.time() - finger_heart_start_time
                            remaining = max(0.0, 1.5 - elapsed)
                            status_text = f"EXITING IN {remaining:.1f}s (Finger Heart)"
                            status_color = (0, 165, 255)
                            if elapsed >= 1.5:
                                print("\n[SYSTEM] Finger Heart exit gesture detected. Exiting...")
                                running = False
                                break
                            continue
                        else:
                            finger_heart_start_time = None

                        # --- Scroll Gesture Action (Index & Middle Extended) ---
                        if is_scrolling_gesture:
                            current_scroll_y = (index_mcp[1] + middle_mcp[1]) / 2.0
                            if scroll_anchor_y is None:
                                scroll_anchor_y = current_scroll_y
                                last_scroll_time = time.time()
                            
                            dy = scroll_anchor_y - current_scroll_y
                            now = time.time()
                            
                            if abs(dy) > 15:
                                if now - last_scroll_time > 0.10:
                                    ticks = int(dy / 8.0)
                                    if ticks != 0:
                                        pyautogui.scroll(ticks)
                                        last_scroll_time = now
                                        trigger_toast(f"SCROLL {'UP' if ticks > 0 else 'DOWN'}", (255, 0, 255))
                            
                            status_text = "SCROLL MODE"
                            status_color = (255, 0, 255)
                            cv2.line(frame, index_tip, middle_tip, (255, 0, 255), 2)
                            hud_actions.append("SCROLL")
                            continue
                        else:
                            scroll_anchor_y = None

                        # --- Volume Gesture Action (Thumb & Pinky Extended) ---
                        if is_volume_gesture:
                            current_vol_y = (thumb_mcp[1] + pinky_mcp[1]) / 2.0
                            if vol_anchor_y is None:
                                vol_anchor_y = current_vol_y
                                last_vol_change_time = time.time()
                            
                            dy = vol_anchor_y - current_vol_y
                            now = time.time()
                            if abs(dy) > 25:
                                if now - last_vol_change_time > 0.15:
                                    if dy > 0:
                                        pyautogui.press('volumeup')
                                        trigger_toast("VOLUME UP", (0, 165, 255))
                                        hud_actions.append("VOL UP")
                                    else:
                                        pyautogui.press('volumedown')
                                        trigger_toast("VOLUME DOWN", (0, 165, 255))
                                        hud_actions.append("VOL DOWN")
                                    last_vol_change_time = now
                                    vol_anchor_y = current_vol_y
                            status_text = "VOLUME MODE"
                            status_color = (0, 165, 255)
                            cv2.line(frame, thumb_tip, pinky_tip, (0, 165, 255), 2)
                            continue
                        else:
                            vol_anchor_y = None

                    # --- Tutorial Onboarding checker (Updates on right hand mouse movement) ---
                    if allow_mouse and tutorial_active:
                        if tutorial_step == 0:
                            cv2.circle(frame, (w // 2, h // 2), 30, (0, 255, 255), 2)
                            cv2.circle(frame, (w // 2, h // 2), 5, (0, 255, 255), -1)
                            dist_to_center = math.hypot(index_tip[0] - w // 2, index_tip[1] - h // 2)
                            if dist_to_center < 30:
                                tutorial_step += 1
                                trigger_toast("STEP 1 PASS", (0, 255, 0))
                        elif tutorial_step == 1:
                            if is_pure_left_click:
                                tutorial_step += 1
                                trigger_toast("STEP 2 PASS", (0, 255, 0))
                        elif tutorial_step == 2:
                            if is_double_clicked_state:
                                tutorial_step += 1
                                trigger_toast("STEP 3 PASS", (0, 255, 0))
                        elif tutorial_step == 3:
                            if is_pure_right_click:
                                tutorial_step += 1
                                trigger_toast("STEP 4 PASS", (0, 255, 0))

                    # --- Tutorial Onboarding checker (Updates on left hand shortcut gestures) ---
                    if allow_shortcuts and tutorial_active:
                        if tutorial_step == 4:
                            if is_scrolling_gesture:
                                tutorial_step += 1
                                trigger_toast("STEP 5 PASS", (0, 255, 0))
                        elif tutorial_step == 5:
                            if is_volume_gesture:
                                tutorial_step += 1
                                trigger_toast("STEP 6 PASS", (0, 255, 0))
                        elif tutorial_step == 6:
                            if is_pure_copy:
                                tutorial_step += 1
                                trigger_toast("STEP 7 PASS", (0, 255, 0))
                        elif tutorial_step == 7:
                            if is_pure_paste:
                                tutorial_step += 1
                                tutorial_active = False
                                trigger_toast("SYSTEM UNLOCKED", (0, 255, 0))

                    # --- 3D Hologram hand representation (3D graphics viewport) ---
                    vis_overlay = frame.copy()
                    cv2.rectangle(vis_overlay, (w - 170, h - 170), (w - 20, h - 20), (30, 30, 30), -1)
                    cv2.addWeighted(vis_overlay, 0.6, frame, 0.4, 0, frame)
                    cv2.rectangle(frame, (w - 170, h - 170), (w - 20, h - 20), color, 1)
                    cv2.putText(frame, "3D Hologram", (w - 160, h - 155),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

                    proj_pts = {}
                    theta = (time.time() * 45) % 360
                    rad = math.radians(theta)
                    cos_val = math.cos(rad)
                    sin_val = math.sin(rad)

                    cx = w - 95
                    cy = h - 95
                    scale_factor = 180.0

                    for k, lm in enumerate(hand_landmarks.landmark):
                        dx = lm.x - hand_landmarks.landmark[0].x
                        dy = lm.y - hand_landmarks.landmark[0].y
                        dz = lm.z - hand_landmarks.landmark[0].z

                        rx = dx * cos_val - dz * sin_val
                        rz = dx * sin_val + dz * cos_val  # noqa: F841
                        ry = dy

                        px = int(cx + rx * scale_factor)
                        py = int(cy + ry * scale_factor)
                        proj_pts[k] = (px, py)

                    for k in range(21):
                        cv2.circle(frame, proj_pts[k], 2, color, -1)

                    for connection in mp_hands.HAND_CONNECTIONS:
                        p1 = connection[0]
                        p2 = connection[1]
                        cv2.line(frame, proj_pts[p1], proj_pts[p2], color, 1)

                except pyautogui.FailSafeException:
                    pass
                except Exception as e:
                    print(f"\n[ERROR] Hand tracking execution error: {e}")
                    pass

            if not running:
                break
        else:
            prev_raw_x = None
            prev_raw_y = None

        # Draw Active Screen Zone boundary box on camera HUD
        x_min_px, x_max_px = int(X_MIN * w), int(X_MAX * w)
        y_min_px, y_max_px = int(Y_MIN * h), int(Y_MAX * h)
        cv2.rectangle(frame, (x_min_px, y_min_px), (x_max_px, y_max_px), (255, 255, 255), 1)

        # Draw Status Bar on Screen
        cv2.rectangle(frame, (0, 0), (w, 40), (0, 0, 0), -1)
        cv2.putText(frame, f"STATUS: {status_text}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)

        # Draw running action notifications on the screen
        if hud_actions:
            action_str = " + ".join(hud_actions)
            cv2.putText(frame, f"ACTION: {action_str}", (10, h - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        # Draw current ratios on the frame for calibration
        if results.multi_hand_landmarks and not is_paused:
            ratio_str = f"Ratios -> idx:{ratio_index:.2f} mid:{ratio_middle:.2f} rng:{ratio_ring:.2f} pky:{ratio_pinky:.2f}"
            cv2.putText(frame, ratio_str, (10, h - 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # --- Interactive Onboarding Tutorial UI Overlay (Visual Checklist) ---
        if tutorial_active:
            overlay = frame.copy()
            cv2.rectangle(overlay, (15, 50), (280, 270), (10, 10, 10), -1)
            cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
            cv2.rectangle(frame, (15, 50), (280, 270), (0, 255, 255), 1)

            cv2.putText(frame, "ONBOARDING TUTORIAL", (25, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

            steps_list = [
                "Move Index to Target",
                "Pinch Index (Left Click)",
                "Double Pinch Index (Dbl Click)",
                "Pinch Middle (Right Click)",
                "Raise Index + Mid (Scroll)",
                "Raise Thumb + Pinky (Volume)",
                "Pinch Ring (Copy)",
                "Pinch Pinky (Paste)"
            ]

            for idx, step_name in enumerate(steps_list):
                y_pos = 100 + idx * 20
                if idx < tutorial_step:
                    cv2.putText(frame, f"[X] {step_name}", (25, y_pos),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
                elif idx == tutorial_step:
                    blink_color = (0, 255, 255) if int(time.time() * 2.5) % 2 == 0 else (0, 180, 255)
                    cv2.putText(frame, f"->  {step_name}", (25, y_pos),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, blink_color, 1)
                else:
                    cv2.putText(frame, f"[ ] {step_name}", (25, y_pos),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1)

            cv2.putText(frame, "Press 's' in window to Skip", (25, 280),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)

        # --- Glowing Action Toast Notification Draw ---
        global action_toast_text, action_toast_time, action_toast_color
        now = time.time()
        if now - action_toast_time < 1.0:
            toast_str = f"[{action_toast_text}]"
            text_size = cv2.getTextSize(toast_str, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
            tx = (w - text_size[0]) // 2
            
            cv2.rectangle(frame, (tx - 15, 45), (tx + text_size[0] + 15, 80), (15, 15, 15), -1)
            cv2.rectangle(frame, (tx - 15, 45), (tx + text_size[0] + 15, 80), action_toast_color, 1)
            cv2.putText(frame, toast_str, (tx, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, action_toast_color, 2)

        # Compress and encode frame for browser MJPEG streaming
        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            with frame_lock:
                latest_frame = jpeg.tobytes()

        # Manage loop pace to prevent high CPU utilization without desktop windowing
        time.sleep(0.01)

    keyboard.clear_all_hotkeys()
    cap.release()
    cv2.destroyAllWindows()
    print("System Cleaned Up. Goodbye!")

if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        print("\n[CRASH] The application encountered a fatal error:")
        traceback.print_exc(file=sys.stdout)
