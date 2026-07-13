import os
import sys
import warnings

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

# Optimize PyAutoGUI settings for responsiveness
pyautogui.FAILSAFE = False  # Disabled to prevent crash when hand first appears or goes out of bounds
pyautogui.PAUSE = 0.001  # Minimum delay to keep cursor movement smooth

# Initialize MediaPipe Hands
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=1,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7
)

# Screen Dimensions
screen_w, screen_h = pyautogui.size()

# Active camera tracking box (normalized coordinates)
# Set to 0.0 and 1.0 to track across the full camera frame (maximum tracking space)
X_MIN, X_MAX = 0.0, 1.0
Y_MIN, Y_MAX = 0.0, 1.0

# Cursor Smoothing (Adaptive Exponential Moving Average)
prev_screen_x, prev_screen_y = pyautogui.position()

# Gesture Thresholds (relative to Hand Reference Distance: Wrist to Index MCP)
PINCH_RATIO_THRESHOLD = 0.22

# State Variables to prevent repeated triggering
is_dragging = False
middle_pinched = False
ring_pinched = False
pinky_pinched = False
is_paused = False

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
    "Raise Index + Middle (Move hand up/down to Scroll)",
    "Raise Thumb + Pinky (Move hand up/down for Volume)",
    "Pinch Ring + Thumb (Copy Action)",
    "Pinch Pinky + Thumb (Paste Action)"
]

def get_distance(p1, p2):
    """Calculate Euclidean distance between two points."""
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

def trigger_toast(text, color=(0, 255, 255)):
    """Set the parameters for the on-screen glowing toast notification."""
    global action_toast_text, action_toast_time, action_toast_color
    action_toast_text = text
    action_toast_time = time.time()
    action_toast_color = color

def main():
    global prev_screen_x, prev_screen_y
    global is_dragging, middle_pinched, ring_pinched, pinky_pinched, is_paused
    global tutorial_active, tutorial_step

    # Persistent Gesture State Variables
    scroll_anchor_y = None
    vol_anchor_y = None
    last_vol_change_time = 0.0
    last_pinch_release_time = 0.0
    is_double_clicked_state = False

    def toggle_pause():
        global is_paused
        is_paused = not is_paused
        print(f"\n[SYSTEM] Gesture Control {'PAUSED' if is_paused else 'RESUMED'}")

    # Register global hotkey (F8) to toggle gesture control
    keyboard.add_hotkey('f8', toggle_pause)

    # Open webcam
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open webcam.")
        return

    # Set camera resolution (standard 640x480)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print("\n" + "="*50)
    print("      ANTIGRAVITY ADVANCED GESTURE CONTROL RUNNING")
    print("="*50)
    print("Core Controls:")
    print(" - Move Index Finger: Move Mouse Cursor")
    print(" - Pinch Index + Thumb: Left Click & Drag (Select)")
    print(" - Double Pinch Index + Thumb: Double Click")
    print(" - Pinch Middle + Thumb: Right Click")
    print(" - Raise Index + Middle: Scroll Mode (Move hand up/down)")
    print(" - Raise Thumb + Pinky: Volume Mode (Move hand up/down)")
    print(" - Pinch Ring + Thumb: Copy (Ctrl + C)")
    print(" - Pinch Pinky + Thumb: Paste (Ctrl + V)")
    print(" - Make Thumbs-Up: Exit Application (Immediate)")
    print(" - Press 'F8' on Keyboard: Pause/Resume tracking")
    print(" - Press 's' in window: Skip onboarding tutorial")
    print("="*50 + "\n")

    running = True
    while cap.isOpened() and running:
        success, frame = cap.read()
        if not success:
            time.sleep(0.1)  # Sleep briefly to avoid high CPU usage if camera is disconnected
            continue

        # Flip the image horizontally for a mirrored natural feel
        frame = cv2.flip(frame, 1)
        h, w, _ = frame.shape

        # Convert BGR image to RGB for MediaPipe processing
        image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands.process(image_rgb)

        # Default HUD overlay variables
        status_text = "No Hand Detected"
        status_color = (0, 0, 255)  # Red
        hud_actions = []

        if is_paused:
            status_text = "PAUSED (F8 to Resume)"
            status_color = (128, 128, 128)  # Grey

        # Define default ratio variables in case hand is not detected
        ratio_index = ratio_middle = ratio_ring = ratio_pinky = 1.0

        if results.multi_hand_landmarks and not is_paused:
            status_text = "Cursor Control"
            status_color = (0, 255, 0)  # Green

            for hand_landmarks in results.multi_hand_landmarks:
                try:
                    # Draw skeleton connections on main frame
                    mp_drawing.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)

                    # Get pixel coordinates for key landmarks
                    def get_pt(lm_id):
                        lm = hand_landmarks.landmark[lm_id]
                        return int(lm.x * w), int(lm.y * h)

                    wrist = get_pt(mp_hands.HandLandmark.WRIST)
                    index_mcp = get_pt(mp_hands.HandLandmark.INDEX_FINGER_MCP)
                    middle_mcp = get_pt(mp_hands.HandLandmark.MIDDLE_FINGER_MCP)
                    ring_mcp = get_pt(mp_hands.HandLandmark.RING_FINGER_MCP)
                    pinky_mcp = get_pt(mp_hands.HandLandmark.PINKY_MCP)
                    thumb_mcp = get_pt(mp_hands.HandLandmark.THUMB_MCP)
                    
                    # Finger tips
                    thumb_tip = get_pt(mp_hands.HandLandmark.THUMB_TIP)
                    index_tip = get_pt(mp_hands.HandLandmark.INDEX_FINGER_TIP)
                    middle_tip = get_pt(mp_hands.HandLandmark.MIDDLE_FINGER_TIP)
                    ring_tip = get_pt(mp_hands.HandLandmark.RING_FINGER_TIP)
                    pinky_tip = get_pt(mp_hands.HandLandmark.PINKY_TIP)

                    # Calculate Scale Reference Distance (Wrist to Index MCP)
                    ref_dist = get_distance(wrist, index_mcp)
                    if ref_dist == 0:
                        ref_dist = 1

                    # Calculate pinch ratios (distance / reference hand size)
                    ratio_index = get_distance(thumb_tip, index_tip) / ref_dist
                    ratio_middle = get_distance(thumb_tip, middle_tip) / ref_dist
                    ratio_ring = get_distance(thumb_tip, ring_tip) / ref_dist
                    ratio_pinky = get_distance(thumb_tip, pinky_tip) / ref_dist

                    # Track Index Finger PIP joint instead of TIP for cursor movement to avoid pinch jitter
                    index_lm = hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_PIP]
                    # Clamp within active tracking box
                    x_clamped = max(X_MIN, min(index_lm.x, X_MAX))
                    y_clamped = max(Y_MIN, min(index_lm.y, Y_MAX))

                    # Interpolate coordinate to full screen resolution
                    target_screen_x = ((x_clamped - X_MIN) / (X_MAX - X_MIN)) * screen_w
                    target_screen_y = ((y_clamped - Y_MIN) / (Y_MAX - Y_MIN)) * screen_h

                    # Calculate movement distance to adaptively adjust smoothing speed (decreases lag during fast movement)
                    move_dist = math.hypot(target_screen_x - prev_screen_x, target_screen_y - prev_screen_y)

                    # Check current pinch state for drag smoothing
                    pinched_index = ratio_index < PINCH_RATIO_THRESHOLD

                    # Adaptive EMA: use higher smoothing (lower alpha) during click/drag for drawing precision
                    if pinched_index or is_dragging:
                        min_alpha, max_alpha = 0.08, 0.45
                    else:
                        min_alpha, max_alpha = 0.12, 0.70
                    
                    min_dist, max_dist = 5.0, 100.0

                    if move_dist < min_dist:
                        current_alpha = min_alpha
                    elif move_dist > max_dist:
                        current_alpha = max_alpha
                    else:
                        current_alpha = min_alpha + (max_alpha - min_alpha) * ((move_dist - min_dist) / (max_dist - min_dist))

                    # Apply Exponential Moving Average (EMA) smoothing using the dynamic alpha
                    smooth_x = int(current_alpha * target_screen_x + (1.0 - current_alpha) * prev_screen_x)
                    smooth_y = int(current_alpha * target_screen_y + (1.0 - current_alpha) * prev_screen_y)

                    # Check for Thumbs-Up Exit Gesture (Mathematical Fold ratios)
                    ratio_index_fold = get_distance(index_tip, index_mcp) / ref_dist
                    ratio_middle_fold = get_distance(middle_tip, middle_mcp) / ref_dist
                    ratio_ring_fold = get_distance(ring_tip, ring_mcp) / ref_dist
                    ratio_pinky_fold = get_distance(pinky_tip, pinky_mcp) / ref_dist
                    ratio_thumb_ext = get_distance(thumb_tip, thumb_mcp) / ref_dist

                    # Forgiving fold check: fingers folded < 0.80, thumb extended > 0.50
                    index_folded_forgiving = ratio_index_fold < 0.80
                    middle_folded_forgiving = ratio_middle_fold < 0.80
                    ring_folded_forgiving = ratio_ring_fold < 0.80
                    pinky_folded_forgiving = ratio_pinky_fold < 0.80

                    # Thumb is extended up (tip Y is smaller than wrist and other finger tips)
                    thumb_tip_y = hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].y
                    wrist_y = hand_landmarks.landmark[mp_hands.HandLandmark.WRIST].y
                    index_tip_y = hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP].y
                    middle_tip_y = hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_TIP].y

                    thumb_is_up = thumb_tip_y < wrist_y and thumb_tip_y < index_tip_y and thumb_tip_y < middle_tip_y
                    thumb_extended = ratio_thumb_ext > 0.50

                    is_thumbs_up = thumb_is_up and thumb_extended and index_folded_forgiving and middle_folded_forgiving and ring_folded_forgiving and pinky_folded_forgiving

                    # Check Scroll Gesture: Index and Middle extended, Ring and Pinky folded, Index and Middle tips close
                    index_extended = ratio_index_fold > 0.7
                    middle_extended = ratio_middle_fold > 0.7
                    index_middle_dist = get_distance(index_tip, middle_tip) / ref_dist
                    is_scrolling_gesture = index_extended and middle_extended and (ratio_ring_fold < 0.6) and (ratio_pinky_fold < 0.6) and (index_middle_dist < 0.5)

                    # Check Volume Gesture: Thumb and Pinky extended, Middle and Ring folded
                    pinky_extended = ratio_pinky_fold > 0.7
                    is_volume_gesture = thumb_extended and pinky_extended and (ratio_middle_fold < 0.6) and (ratio_ring_fold < 0.6)

                    # Check Pinch states for other fingers
                    pinched_middle = ratio_middle < PINCH_RATIO_THRESHOLD
                    pinched_ring = ratio_ring < PINCH_RATIO_THRESHOLD
                    pinched_pinky = ratio_pinky < PINCH_RATIO_THRESHOLD

                    # --- Exit / Thumbs up Logic ---
                    if is_thumbs_up:
                        print("\n[SYSTEM] Thumbs-Up exit gesture detected. Exiting...")
                        running = False
                        break
                        continue

                    # --- Scroll Gesture Action ---
                    if is_scrolling_gesture:
                        current_scroll_y = (index_mcp[1] + middle_mcp[1]) / 2.0
                        if scroll_anchor_y is None:
                            scroll_anchor_y = current_scroll_y
                        
                        dy = scroll_anchor_y - current_scroll_y
                        if abs(dy) > 20:
                            # Velocity based proportional scrolling
                            scroll_ticks = int(dy / 8.0)
                            if scroll_ticks != 0:
                                pyautogui.scroll(scroll_ticks)
                                trigger_toast("SCROLLING", (255, 0, 255))
                                scroll_anchor_y = current_scroll_y
                        status_text = "SCROLL MODE"
                        status_color = (255, 0, 255)  # Magenta
                        cv2.line(frame, index_tip, middle_tip, (255, 0, 255), 2)
                        hud_actions.append("SCROLL")
                        continue
                    else:
                        scroll_anchor_y = None

                    # --- Volume Gesture Action ---
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
                        status_color = (0, 165, 255)  # Orange
                        cv2.line(frame, thumb_tip, pinky_tip, (0, 165, 255), 2)
                        continue
                    else:
                        vol_anchor_y = None

                    # --- Left Click, Double Click & Drag Actions ---
                    if pinched_index:
                        if not is_dragging and not is_double_clicked_state:
                            now = time.time()
                            # Check double pinch timing (within 350ms)
                            if now - last_pinch_release_time < 0.35:
                                pyautogui.doubleClick()
                                is_double_clicked_state = True
                                trigger_toast("DOUBLE CLICK", (255, 255, 0))
                                hud_actions.append("DOUBLE CLICK")
                                print("[ACTION] Double Click")
                            else:
                                pyautogui.mouseDown()
                                is_dragging = True
                                trigger_toast("LEFT CLICK (DOWN)", (0, 255, 0))
                                print("[ACTION] Mouse Down (Drag Start)")
                        status_text = "DRAGGING" if is_dragging else "DOUBLE CLICK"
                        status_color = (255, 255, 0)  # Cyan
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
                    if pinched_middle:
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
                    if pinched_ring:
                        cv2.line(frame, thumb_tip, ring_tip, (255, 0, 255), 3)
                        if not ring_pinched:
                            pyautogui.hotkey('ctrl', 'c')
                            ring_pinched = True
                            trigger_toast("COPY (Ctrl+C)", (255, 0, 255))
                            hud_actions.append("COPY")
                            print("[ACTION] Copy (Ctrl + C)")
                    else:
                        ring_pinched = False
                        cv2.line(frame, thumb_tip, ring_tip, (0, 255, 0), 1)

                    # --- Paste Action (Pinky finger) ---
                    if pinched_pinky:
                        cv2.line(frame, thumb_tip, pinky_tip, (0, 255, 255), 3)
                        if not pinky_pinched:
                            pyautogui.hotkey('ctrl', 'v')
                            pinky_pinched = True
                            trigger_toast("PASTE (Ctrl+V)", (255, 255, 0))
                            hud_actions.append("PASTE")
                            print("[ACTION] Paste (Ctrl + V)")
                    else:
                        pinky_pinched = False
                        cv2.line(frame, thumb_tip, pinky_tip, (0, 255, 0), 1)

                    # Move Mouse if we are dragging or just moving around (and not executing any static shortcut action)
                    is_other_action = pinched_middle or pinched_ring or pinched_pinky
                    if not is_other_action:
                        pyautogui.moveTo(smooth_x, smooth_y)
                        prev_screen_x, prev_screen_y = smooth_x, smooth_y

                except pyautogui.FailSafeException:
                    pass
                except Exception as e:
                    # Print tracking/pyautogui errors but do NOT exit the program
                    print(f"\n[ERROR] Hand tracking execution error: {e}")
                    pass

                # --- Tutorial Target Checker ---
                if tutorial_active:
                    if tutorial_step == 0:
                        # Draw glowing target in center of camera frame
                        cv2.circle(frame, (w // 2, h // 2), 30, (0, 255, 255), 2)
                        cv2.circle(frame, (w // 2, h // 2), 5, (0, 255, 255), -1)
                        # Check index finger tip distance to center of frame
                        dist_to_center = math.hypot(index_tip[0] - w // 2, index_tip[1] - h // 2)
                        if dist_to_center < 30:
                            tutorial_step += 1
                            trigger_toast("STEP 1 PASS", (0, 255, 0))
                            print("[TUTORIAL] Step 1 Complete: Cursor Movement!")
                    elif tutorial_step == 1:
                        if pinched_index:
                            tutorial_step += 1
                            trigger_toast("STEP 2 PASS", (0, 255, 0))
                            print("[TUTORIAL] Step 2 Complete: Left Click!")
                    elif tutorial_step == 2:
                        if is_double_clicked_state:
                            tutorial_step += 1
                            trigger_toast("STEP 3 PASS", (0, 255, 0))
                            print("[TUTORIAL] Step 3 Complete: Double Click!")
                    elif tutorial_step == 3:
                        if pinched_middle:
                            tutorial_step += 1
                            trigger_toast("STEP 4 PASS", (0, 255, 0))
                            print("[TUTORIAL] Step 4 Complete: Right Click!")
                    elif tutorial_step == 4:
                        if is_scrolling_gesture:
                            tutorial_step += 1
                            trigger_toast("STEP 5 PASS", (0, 255, 0))
                            print("[TUTORIAL] Step 5 Complete: Scrolling!")
                    elif tutorial_step == 5:
                        if is_volume_gesture:
                            tutorial_step += 1
                            trigger_toast("STEP 6 PASS", (0, 255, 0))
                            print("[TUTORIAL] Step 6 Complete: Volume Control!")
                    elif tutorial_step == 6:
                        if pinched_ring:
                            tutorial_step += 1
                            trigger_toast("STEP 7 PASS", (0, 255, 0))
                            print("[TUTORIAL] Step 7 Complete: Copy Action!")
                    elif tutorial_step == 7:
                        if pinched_pinky:
                            tutorial_step += 1
                            tutorial_active = False  # Onboarding completes on Paste!
                            trigger_toast("SYSTEM UNLOCKED", (0, 255, 0))
                            print("[TUTORIAL] Onboarding Complete! System Unlocked.")

                # --- 3D Hologram hand representation (3D graphics viewport) ---
                # Glassmorphic card background
                vis_overlay = frame.copy()
                cv2.rectangle(vis_overlay, (w - 170, h - 170), (w - 20, h - 20), (30, 30, 30), -1)
                cv2.addWeighted(vis_overlay, 0.6, frame, 0.4, 0, frame)
                cv2.rectangle(frame, (w - 170, h - 170), (w - 20, h - 20), (0, 255, 255), 1)
                cv2.putText(frame, "3D Hologram", (w - 160, h - 155),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

                # Project and draw 3D rotating hand skeleton
                proj_pts = {}
                theta = (time.time() * 45) % 360  # Spin at 45 degrees per second
                rad = math.radians(theta)
                cos_val = math.cos(rad)
                sin_val = math.sin(rad)

                cx = w - 95
                cy = h - 95
                scale_factor = 180.0  # Project landmarks centered around the wrist (ID 0)

                for k, lm in enumerate(hand_landmarks.landmark):
                    dx = lm.x - hand_landmarks.landmark[0].x
                    dy = lm.y - hand_landmarks.landmark[0].y
                    dz = lm.z - hand_landmarks.landmark[0].z

                    # Rotate around Y-axis (Spin)
                    rx = dx * cos_val - dz * sin_val
                    rz = dx * sin_val + dz * cos_val  # noqa: F841
                    ry = dy

                    # Project 3D coordinate to 2D screen coordinate
                    px = int(cx + rx * scale_factor)
                    py = int(cy + ry * scale_factor)
                    proj_pts[k] = (px, py)

                # Draw joints
                for k in range(21):
                    cv2.circle(frame, proj_pts[k], 2, (0, 255, 255), -1)

                # Draw skeleton connections
                for connection in mp_hands.HAND_CONNECTIONS:
                    p1 = connection[0]
                    p2 = connection[1]
                    cv2.line(frame, proj_pts[p1], proj_pts[p2], (255, 255, 0), 1)

            if not running:
                break

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
            # Draw semi-transparent background panel
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
                "Raise 2 Fingers (Scroll)",
                "Rock-on (Volume Control)",
                "Pinch Ring (Copy)",
                "Pinch Pinky (Paste)"
            ]

            for idx, step_name in enumerate(steps_list):
                y_pos = 100 + idx * 20
                if idx < tutorial_step:
                    # Completed step (Green Check)
                    cv2.putText(frame, f"[X] {step_name}", (25, y_pos),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
                elif idx == tutorial_step:
                    # Active step (Blinking Yellow)
                    blink_color = (0, 255, 255) if int(time.time() * 2.5) % 2 == 0 else (0, 180, 255)
                    cv2.putText(frame, f"->  {step_name}", (25, y_pos),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, blink_color, 1)
                else:
                    # Locked step (Grey)
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
            
            # Glowing toast container box
            cv2.rectangle(frame, (tx - 15, 45), (tx + text_size[0] + 15, 80), (15, 15, 15), -1)
            cv2.rectangle(frame, (tx - 15, 45), (tx + text_size[0] + 15, 80), action_toast_color, 1)
            cv2.putText(frame, toast_str, (tx, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, action_toast_color, 2)

        # Display the output window
        cv2.imshow("Antigravity Gesture Control", frame)

        # Check key inputs (Wait 1ms for window processing, but do not exit on 'q' or window close)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('s') and tutorial_active:
            tutorial_active = False
            print("\n[SYSTEM] Tutorial skipped. System unlocked!\n")
            trigger_toast("SYSTEM UNLOCKED", (0, 255, 0))

    # Cleanup
    keyboard.clear_all_hotkeys()
    cap.release()
    cv2.destroyAllWindows()
    print("System Cleaned Up. Goodbye!")

if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        # Since standard error is redirected, we manually print traceback to standard output (stdout)
        # to ensure any Python-level crashes are still displayed cleanly to the user.
        print("\n[CRASH] The application encountered a fatal error:")
        traceback.print_exc(file=sys.stdout)
