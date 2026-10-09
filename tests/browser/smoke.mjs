import { chromium } from 'playwright';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {randomBytes, webcrypto} from 'node:crypto';
import {readFile, unlink} from 'node:fs/promises';
import assert from 'node:assert/strict';

const admin = randomBytes(32).toString('hex');
const password = randomBytes(32).toString('hex');
const backend = spawn(process.env.PYTHON || 'python', ['-c', `
import os, threading
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import uvicorn
from token_abuse_engine.config import load_config, StorageConfig
from token_abuse_engine.gateway import create_app
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass
static = ThreadingHTTPServer(('127.0.0.1', 8081), partial(QuietHandler, directory='pages'))
threading.Thread(target=static.serve_forever, daemon=True).start()
config = load_config('config.yaml')
config.storage = StorageConfig(type='memory')
config.providers = []
uvicorn.run(create_app(config), host='127.0.0.1', port=8080, access_log=False)
`], {cwd: fileURLToPath(new URL('../../', import.meta.url)), env: {...process.env, ADMIN_API_KEY: admin, GATEWAY_API_KEY: randomBytes(32).toString('hex')}, stdio: ['ignore', 'ignore', 'pipe']});
let browser;
try {
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Backend startup timeout')), 15000);
    backend.stderr.on('data', data => {
      if (data.toString().includes('Uvicorn running')) { clearTimeout(timer); resolve(); }
    });
    backend.on('exit', () => { clearTimeout(timer); reject(new Error('Backend exited')); });
  });
  assert.equal((await fetch('http://127.0.0.1:8080/health')).status, 200);
  assert.equal((await fetch('http://127.0.0.1:8080/v1/models')).status, 401);
  browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || undefined,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
    headless: true,
  });
  const page = await browser.newPage({acceptDownloads: true});
  const errors = [];
  page.on('pageerror', () => errors.push('pageerror'));
  await page.goto('http://localhost:8081');
  await page.locator('#gateway').fill('http://127.0.0.1:8080');
  await page.locator('#admin').fill(admin);
  await page.locator('#name').fill('browser-smoke');
  await page.locator('#password').fill(password);
  await page.locator('#confirm').fill(password);
  const responsePromise = page.waitForResponse(r => r.url().endsWith('/admin/api-keys') && r.request().method() === 'POST');
  await page.locator('#submit').click();
  const response = await responsePromise;
  assert.equal(response.status(), 201);
  const issued = (await response.json()).api_key;
  await page.locator('#download').waitFor({state: 'visible'});
  for (const id of ['admin', 'password', 'confirm']) assert.ok(await page.locator(`#${id}`).inputValue() === '');
  const storage = await page.evaluate(() => ({local: {...localStorage}, session: {...sessionStorage}}));
  assert.deepEqual(storage, {local: {}, session: {}});
  assert.ok(!(await page.locator('body').innerText()).includes(issued));
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download').click();
  const download = await downloadPromise;
  const text = await readFile(await download.path(), 'utf8');
  assert.ok(!text.includes(issued) && !text.includes(admin) && !text.includes(password));
  const envelope = JSON.parse(text);
  assert.equal(envelope.iterations, 310000);
  const material = await webcrypto.subtle.importKey('raw', Buffer.from(password), 'PBKDF2', false, ['deriveKey']);
  const key = await webcrypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt:Buffer.from(envelope.salt,'base64'),iterations:envelope.iterations},material,{name:'AES-GCM',length:256},false,['decrypt']);
  const plain = await webcrypto.subtle.decrypt({name:'AES-GCM',iv:Buffer.from(envelope.iv,'base64')},key,Buffer.from(envelope.ciphertext,'base64'));
  assert.ok(Buffer.from(plain).toString() === issued);
  assert.equal((await fetch('http://127.0.0.1:8080/v1/models', {headers: {Authorization: `Bearer ${issued}`}})).status, 200);
  await unlink(await download.path());
  assert.equal(errors.length, 0);
  await page.reload();
  assert.ok(await page.locator('#admin').inputValue() === '');
  assert.ok(await page.locator('#download').isHidden());
  console.log('PASS: Chromium issuance, cross-origin CORS, encrypted download, real-key decryption, authentication, empty browser storage and cleared credentials.');
} finally {
  await browser?.close();
  backend.kill('SIGTERM');
}
