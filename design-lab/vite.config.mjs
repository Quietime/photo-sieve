import {defineConfig} from 'vite';
export default defineConfig({build:{target:'es2022',rollupOptions:{output:{manualChunks:{'theatre':['@theatre/core','@theatre/studio'],'lottie':['lottie-web'],'three':['three','postprocessing']}}}}});
