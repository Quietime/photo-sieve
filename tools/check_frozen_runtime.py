"""Exercise installed EXE, real Qt event loop, folder dialog and repeated RAW switches."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.raw_fixture import write_raw

def run(args):
    output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='photosieve-runtime-') as temp:
        base=Path(temp);install=base/'Installed APP';shutil.copytree(args.exe.resolve().parent,install)
        small=base/'10 RAW';large=base/'16000 RAW';small.mkdir();large.mkdir()
        template=base/'template.dng';write_raw(template);seed=template
        for folder,count in ((small,10),(large,16000)):
            for i in range(count):
                if i%500==0:
                    seed=base/f'{folder.name}-{i}.dng';shutil.copyfile(template,seed)
                os.link(seed,folder/f'{i:05}.arw')
            (folder/'audio.wav').write_bytes(b'ignore audio')
        # Reopen both folders after their QFileDialog/old library have gone away.
        paths=[small,large,small,large]
        command=[str(install/args.exe.name),'--runtime-check-output',str(output),'--runtime-check-folders',*(str(p) for p in paths)]
        env=os.environ.copy();env['QT_QPA_PLATFORM']='windows' if os.name=='nt' else 'offscreen'
        env['PHOTOSIEVE_DECODERS']='12';env['QT_WIDGETS_RHI']='0'
        child=subprocess.Popen(command,env=env)
        try:
            code=child.wait(timeout=200)
            if code:raise AssertionError(f'Frozen normal runtime exit {code}')
            report=json.loads(output.read_text('utf-8'))
            assert report['ok'] and [r['photos'] for r in report['folders']]==[10,16000,10,16000],report
            print('PASS: normal frozen EXE, QApplication.exec, real folder picker, 12 decoders, 10/16000 RAW repeated switch',flush=True)
        finally:
            logs=install/'workspaces'/'logs'
            if logs.is_dir():shutil.copytree(logs,output.with_name(output.stem+'-logs'),dirs_exist_ok=True,ignore=shutil.ignore_patterns('*.tmp'))
            if child.poll() is None:child.kill();child.wait()
    return 0
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--exe',type=Path,default=Path('dist/PhotoSieve/PhotoSieve.exe'))
    parser.add_argument('--output',type=Path,default=Path('frozen-runtime-ci.json'))
    raise SystemExit(run(parser.parse_args()))
