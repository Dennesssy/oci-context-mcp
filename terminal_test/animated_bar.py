import time
import os
import sys

def clear_screen():
    """Clears the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')

def draw_bar(current_value, max_value, bar_width=40):
    """Draws a horizontal ASCII art bar based on the current_value."""
    current_value = max(0, min(current_value, max_value))
    num_filled_blocks = int(round((current_value / max_value) * bar_width))
    filled_part = '█' * num_filled_blocks
    empty_part = ' ' * (bar_width - num_filled_blocks)
    return f"[{filled_part}{empty_part}] Value: {current_value}/{max_value}"

if __name__ == "__main__":
    max_val = 50
    step_size = 5
    frame_delay = 0.1
    
    start_time = time.time()
    run_duration = 10 # seconds

    print("Simulating a bar going up and down. Running for 10 seconds.")
    time.sleep(1)

    try:
        while (time.time() - start_time) < run_duration:
            # Bar going up
            for val in range(0, max_val + 1, step_size):
                if (time.time() - start_time) >= run_duration:
                    break
                clear_screen()
                print(draw_bar(val, max_val))
                time.sleep(frame_delay)

            # Bar going down
            for val in range(max_val - step_size, -1, -step_size): 
                if (time.time() - start_time) >= run_duration:
                    break
                clear_screen()
                print(draw_bar(val, max_val))
                time.sleep(frame_delay)

    except KeyboardInterrupt:
        pass # Allow Ctrl+C to stop it earlier if desired
    finally:
        clear_screen()
        print("Animation stopped.")
        print(draw_bar(0, max_val)) # Ensure it ends at 0
        sys.exit(0) # Exit cleanly after the duration or interrupt
