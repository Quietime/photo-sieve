"""Entry point of the desktop app. Kept tiny on purpose: decoder processes (multiprocessing spawn) start from this
script too, and must not load Qt or the interface just to decode photos."""
import multiprocessing
import sys

if __name__ == '__main__':
    multiprocessing.freeze_support()   # decoder processes of the packaged app branch off here
    from desktop import main
    sys.exit(main())
