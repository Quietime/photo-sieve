from pathlib import Path
import base64,json,re,shutil
import sys
root=Path(__file__).resolve().parent;out=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/workspace/shared/downloads/design-tools/proposal');out.mkdir(parents=True,exist_ok=True)
p=root/'proposal.mjs';s=p.read_text().replace("function historyAPI(u){window.history.replaceState(null,'',u);}","function historyAPI(u){if(location.protocol==='http:'||location.protocol==='https:')window.history.replaceState(null,'',u);}");p.write_text(s)
art={f.stem:'data:image/'+('png' if f.suffix=='.png' else 'webp')+';base64,'+base64.b64encode(f.read_bytes()).decode() for f in (root/'assets').iterdir() if f.is_file()}
js=s.replace("src:`./assets/${i%3===1?'landscape':i%3===2?'sample':'portrait'}.webp`","src:ART[i%3===1?'landscape':i%3===2?'sample':'portrait']").replace("image.src=`./assets/${name}.webp`","image.src=ART[name]")
js='const ART='+json.dumps(art)+';\n'+js
html=(root/'index.html').read_text().replace('<link rel="stylesheet" href="./proposal.css">','<style>'+(root/'proposal.css').read_text()+'</style>').replace('<script type="module" src="./proposal.mjs"></script>','<script type="module">'+js+'</script>').replace('./PLAN.md','PhotoSieve-UI-Optimization-Plan.md')
for name,data in art.items():
 for suffix in ['webp','png']:html=html.replace('./assets/'+name+'.'+suffix,data)
html=html.replace('/proposal/assets/face.webp',art['face'])
(out/'PhotoSieve-UI-Optimization-Preview.html').write_text(html)
shutil.copyfile(root/'PLAN.md',out/'PhotoSieve-UI-Optimization-Plan.md')
print('Standalone HTML:',len(html.encode()),'bytes')
