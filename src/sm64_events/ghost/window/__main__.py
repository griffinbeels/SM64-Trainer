"""`python -m sm64_events.ghost.window` -- the overlay child process."""
import sys

from sm64_events.ghost.window.child import main

if __name__ == "__main__":
    sys.exit(main())
