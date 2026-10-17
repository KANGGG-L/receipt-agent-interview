#!/usr/bin/env node
/**
 * UAT020: static contract — batch photo drawer must not steal rotate/crop hits.
 * Usage: node demo/tests/frontend_uat020_drawer_rotate_harness.js
 * Independent QA/UAT must still prove elementFromPoint + real click at 1440/390/412/1024.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
let pass = 0;
let fail = 0;
function check(name, cond) {
  if (cond) { pass += 1; console.log('[PASS]', name); }
  else { fail += 1; console.log('[FAIL]', name); }
}

const css = fs.readFileSync(path.join(ROOT, 'demo/static/css/style.css'), 'utf8');
const mob = fs.readFileSync(path.join(ROOT, 'demo/static/css/review-mobile.css'), 'utf8');
const js = fs.readFileSync(path.join(ROOT, 'demo/static/js/upload.js'), 'utf8');
const html = fs.readFileSync(path.join(ROOT, 'demo/templates/index.html'), 'utf8');

check('btnRotate present in HTML', /id="btnRotate"/.test(html));
check('imgToolbar present in HTML', /id="imgToolbar"/.test(html));
check('photoSider drawer present in HTML', /id="photoSider"[^>]*class="[^"]*photo-sider-drawer/.test(html));

check('style.css img-toolbar z-index >= 110', /\.img-toolbar\s*\{[^}]*z-index:\s*110/s.test(css));
check('style.css imgViewerContainer z-index 110', /#imgViewerContainer[\s\S]{0,120}z-index:\s*110/.test(css));
check('style.css drawer z-index remains 95', /\.photo-sider-drawer\s*\{[^}]*z-index:\s*95/s.test(css));
check('style.css open drawer pointer-events none', /\.photo-sider-drawer\.open\s*\{[^}]*pointer-events:\s*none/s.test(css));
check('style.css open drawer items pointer-events auto', /\.photo-sider-drawer\.open\s+\.photo-sider-item[\s\S]{0,80}pointer-events:\s*auto/.test(css));
check('style.css open drawer header pointer-events auto', /\.photo-sider-drawer\.open\s+\.sider-header[\s\S]{0,220}pointer-events:\s*auto/.test(css));

check('review-mobile UAT020 media block present', /UAT020/.test(mob) && /max-width:\s*1024px/.test(mob));
check('review-mobile elevates img-toolbar', /review-mobile\.css/.test('review-mobile.css') && /\.img-toolbar[\s\S]{0,200}z-index:\s*110/.test(mob));
check('review-mobile open drawer pointer-events none', /\.photo-sider-drawer\.open\s*\{[^}]*pointer-events:\s*none/s.test(mob));

check('upload.js ensureImgToolsHitLayer', /function ensureImgToolsHitLayer\s*\(/.test(js));
check('upload.js syncPhotoSiderClearanceForImgTools', /function syncPhotoSiderClearanceForImgTools\s*\(/.test(js));
check('upload.js refresh called from updateToolbarButtonStates', /function updateToolbarButtonStates\s*\(\s*\)\s*\{[\s\S]{0,80}refreshUat020ImgToolsVsDrawer\s*\(/.test(js));
check('upload.js data-uat020 img-tools-layer', /data-uat020['"],\s*['"]img-tools-layer['"]/.test(js) || /setAttribute\('data-uat020',\s*'img-tools-layer'\)/.test(js));
check('upload.js bake keeps originalFile', /UAT020 \/ rotate bake[\s\S]{0,120}originalFile/.test(js));
check('upload.js rotateImg still bakes via bakeCurrentRotation', /function rotateImg\s*\([\s\S]*?bakeCurrentRotation\s*\(/.test(js));
check('upload.js no force click / dispatchEvent fake rotate', ! /dispatchEvent\s*\(\s*new\s+MouseEvent\s*\(\s*['"]click['"]/.test(js) || ! /btnRotate[\s\S]{0,200}dispatchEvent/.test(js));

// Negative: closed drawer still has pointer-events none via :not(.open)
check('closed drawer pointer-events none retained', /\.photo-sider-drawer:not\(\.open\)\s*\{[^}]*pointer-events:\s*none/s.test(css));

console.log('');
console.log(pass + ' PASS / ' + fail + ' FAIL');
process.exit(fail ? 1 : 0);
