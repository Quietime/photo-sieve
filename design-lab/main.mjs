import { gsap } from 'gsap';
import lottie from 'lottie-web';
import { createMorph } from 'morphicons/dom';
import { getProject } from '@theatre/core';
import studio from '@theatre/studio';
import * as THREE from 'three';
import { EffectComposer, RenderPass, EffectPass, BloomEffect } from 'postprocessing';
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
const names=['morphicons','lottie-web','gsap','@theatre/core','@theatre/studio','postprocessing'];
const icon=createMorph(document.querySelector('path'),'M4 6h16M4 12h16M4 18h16',{reducedMotion:'user'});
let checked=false;
document.querySelector('#morph').onclick=()=>{checked=!checked;icon.morphTo(checked?'M4 12l5 5L20 6':'M4 6h16M4 12h16M4 18h16');};
document.querySelector('#move').onclick=()=>gsap.fromTo('.glass',{y:0},{y:-4,duration:reduced?0:.16,yoyo:true,repeat:1,ease:'power2.out'});
const animation=lottie.loadAnimation({container:document.querySelector('#lottie'),renderer:'svg',loop:false,autoplay:false,animationData:{v:'5.7.4',fr:60,ip:0,op:60,w:240,h:160,layers:[{ddd:0,ind:1,ty:4,nm:'Preview',sr:1,ks:{o:{a:0,k:100},r:{a:0,k:0},p:{a:1,k:[{t:0,s:[55,80,0],e:[185,80,0],o:{x:.3,y:0},i:{x:.7,y:1}},{t:60,s:[185,80,0]}]},a:{a:0,k:[0,0,0]},s:{a:0,k:[100,100,100]}},shapes:[{ty:'el',p:{a:0,k:[0,0]},s:{a:0,k:[28,28]},nm:'Circle'},{ty:'fl',c:{a:0,k:[.55,.71,.84,1]},o:{a:0,k:100}}],ip:0,op:60,st:0,bm:0}]}});
document.querySelector('#play-lottie').onclick=()=>reduced?animation.goToAndStop(59,true):animation.goToAndPlay(0,true);
await studio.initialize();studio.ui.hide();
const project=getProject('PhotoSieve motion tools');await project.ready;
const sheet=project.sheet('Material');
const marker=sheet.object('Reflection',{position:0});
marker.onValuesChange(({position})=>{document.querySelector('#theatre-marker').style.left=`${12+(document.querySelector('.track').clientWidth-56)*position}px`;});
document.querySelector('#timeline').oninput=e=>{studio.transaction(({set})=>set(marker.props.position,Number(e.target.value)));};
document.querySelector('#studio').onclick=()=>studio.ui.restore();
let composer,renderer;
document.querySelector('#render').onclick=()=>{
  const canvas=document.querySelector('#scene');
  if(!renderer){
    renderer=new THREE.WebGLRenderer({canvas,antialias:true});renderer.setSize(canvas.clientWidth,160,false);
    const scene=new THREE.Scene();scene.background=new THREE.Color('#282d34');
    const camera=new THREE.PerspectiveCamera(40,canvas.width/160,.1,100);camera.position.z=4;
    const mesh=new THREE.Mesh(new THREE.TorusGeometry(.7,.13,20,80),new THREE.MeshBasicMaterial({color:'#83b2df'}));mesh.rotation.x=.5;scene.add(mesh);
    composer=new EffectComposer(renderer);composer.addPass(new RenderPass(scene,camera));composer.addPass(new EffectPass(camera,new BloomEffect({intensity:.2,luminanceThreshold:.2})));
  }
  composer.render();window.labVerified.webgl=true;
};
window.labVerified={imports:names,lottie:!!animation,morph:!!icon,theatre:!!marker,webgl:false};
const status=document.querySelector('#status');status.dataset.ok='true';status.textContent='6 项工具均已加载；按按钮验证动效与编辑器。';
window.addEventListener('pagehide',()=>{animation.destroy();icon.destroy();composer?.dispose();renderer?.dispose();},{once:true});
