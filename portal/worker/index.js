import legacy from './legacy.js';
import {cloudFetch} from './cloud.js';
export {cleanUrl,publicState,FRESH_MS} from './legacy.js';
export default {fetch(request,env){return env.BUCKET ? cloudFetch(request,env) : legacy.fetch(request,env)}};
