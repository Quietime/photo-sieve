import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const out=process.env.PHOTOSIEVE_LAB_OUTPUT||'/workspace/shared/downloads/design-tools';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
const page=await browser.newPage({viewport:{width:1120,height:850}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(process.env.PHOTOSIEVE_LAB_URL||'http://127.0.0.1:4178/');
 await page.waitForFunction(()=>window.labVerified);
 assert.equal((await page.evaluate(()=>window.labVerified.imports)).length,6);
 const initial=await page.locator('svg path').first().getAttribute('d');
 await page.click('#morph');await page.waitForTimeout(350);
 assert.notEqual(await page.locator('svg path').first().getAttribute('d'),initial);
 await page.click('#move');await page.click('#play-lottie');await page.waitForTimeout(1100);
 assert.equal(await page.locator('#lottie svg').count(),1);
 await page.locator('#timeline').fill('0.7');
 await page.waitForFunction(()=>parseFloat(getComputedStyle(document.querySelector('#theatre-marker')).left)>50);
 await page.click('#render');assert.equal(await page.evaluate(()=>window.labVerified.webgl),true);
 await page.screenshot({path:out+'/web-tools-desktop.png',fullPage:true});
 await page.click('#studio');await page.waitForTimeout(400);
 await page.screenshot({path:out+'/theatre-studio.png',fullPage:true});
 await page.reload();await page.waitForFunction(()=>window.labVerified);
 for(const width of [700,390]){
  await page.setViewportSize({width,height:850});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:out+`/web-tools-${width}.png`,fullPage:true});
 }
 await page.emulateMedia({reducedMotion:'reduce'});await page.reload();await page.waitForFunction(()=>window.labVerified);
 await page.click('#move');await page.click('#play-lottie');
 assert.equal(await page.evaluate(()=>matchMedia('(prefers-reduced-motion: reduce)').matches),true);
 assert.deepEqual(errors,[]);
 const report={...await page.evaluate(()=>window.labVerified),webgl:true,browser:'Chromium',widths:[1120,700,390],reducedMotion:true,errors};
 await writeFile(out+'/web-verification.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
