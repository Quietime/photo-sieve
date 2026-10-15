"""Entry point of the desktop app. Kept tiny on purpose: decoder processes (multiprocessing spawn) start from this
script too, and must not load Qt or the interface just to decode photos."""
import multiprocessing
import sys

if __name__ == '__main__':
    multiprocessing.freeze_support()   # decoder processes of the packaged app branch off here
    if len(sys.argv)==3 and sys.argv[1]=='--rating-zoom-self-test':
        from tools.check_rating_zoom import run
        run(sys.argv[2]);sys.exit(0)
    if len(sys.argv)==3 and sys.argv[1]=='--rating-cache-self-test':
        from tools.check_rating_cache import run
        run(sys.argv[2]);sys.exit(0)
    if 3<=len(sys.argv)<=4 and sys.argv[1]=='--cache-full-self-test':
        from tools.check_full_cache import run
        run(sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None);sys.exit(0)
    if len(sys.argv)==3 and sys.argv[1]=='--zoom-motion-self-test':
        from tools.check_zoom_motion import run
        run(sys.argv[2]);sys.exit(0)
    if len(sys.argv)==3 and sys.argv[1]=='--mode-settings-self-test':
        from tools.check_mode_settings import run
        run(sys.argv[2]);sys.exit(0)
    if len(sys.argv)==3 and sys.argv[1]=='--deleted-album-self-test':
        from tools.check_deleted_album import run
        run(sys.argv[2]);sys.exit(0)
    if len(sys.argv)==3 and sys.argv[1]=='--cache-settings-self-test':
        from tools.check_cache_settings import run
        run(sys.argv[2]);sys.exit(0)
    if 3<=len(sys.argv)<=5 and sys.argv[1]=='--delete-sharing-self-test':
        from tools.check_fast_delete import run
        run(sys.argv[2],int(sys.argv[3]) if len(sys.argv)>3 else 24,sys.argv[4] if len(sys.argv)>4 else None);sys.exit(0)
    if 3<=len(sys.argv)<=4 and sys.argv[1]=='--review-ui-self-test':
        from tools.check_review_details import run
        run(sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None);sys.exit(0)
    if len(sys.argv)==3 and sys.argv[1]=='--features-self-test':
        from pathlib import Path
        from tools.check_features import run
        run(Path(sys.argv[2]));sys.exit(0)
    if len(sys.argv)==4 and sys.argv[1]=='--arw-self-test':
        from tools.check_arw_rating import verify
        verify(sys.argv[2],sys.argv[3]);sys.exit(0)
    if len(sys.argv)==3 and sys.argv[1]=='--update-offer-self-test':
        from tools.check_update_offer import run
        sys.exit(run(sys.argv[2]))
    if len(sys.argv)==3 and sys.argv[1]=='--apply-update':
        # The updater replaces Qt DLLs: it must not import the desktop or load a GUI runtime.
        from updater import helper_entry
        sys.exit(helper_entry(sys.argv[2]))
    if len(sys.argv)==3 and sys.argv[1]=='--rating-self-test':
        from rating_diagnostics import run
        sys.exit(run(sys.argv[2]))
    if len(sys.argv)==4 and sys.argv[1]=='--crash-watch':
        from crashlog import watch_process
        sys.exit(watch_process(int(sys.argv[2]),sys.argv[3]))
    import crashlog
    normal=not any(argument in sys.argv for argument in ('--apply-update','--self-test-output'))
    if normal:crashlog.boot()
    try:
        from desktop import main
        code=main()
    except BaseException:
        crashlog.LOGGER.exception('Application terminated by an exception');crashlog.finish(1)
        raise
    else:
        crashlog.finish(code);sys.exit(code)
