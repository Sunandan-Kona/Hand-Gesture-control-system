def print_right_triangle(n):
    """Prints a right-angled triangle of height n."""
    for i in range(1, n + 1):
        print('*' * i)

def print_equilateral_triangle(n):
    """Prints an equilateral triangle of height n."""
    for i in range(1, n + 1):
        # Calculate spaces and stars
        spaces = ' ' * (n - i)
        stars = '*' * (2 * i - 1)
        print(spaces + stars)

if __name__ == '__main__':
    height = 5
    print(f"Right-Angled Triangle (height={height}):")
    print_right_triangle(height)
    print(f"\nEquilateral Triangle (height={height}):")
    print_equilateral_triangle(height)
