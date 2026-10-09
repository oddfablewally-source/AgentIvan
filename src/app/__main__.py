"""
Allow running the application as a module:

    python -m src.app

This delegates to main.py's main() function.
"""

from main import main

if __name__ == "__main__":
    main()
