import assert from 'node:assert/strict';
import {test} from 'node:test';
import {encryptKey} from '../pages/crypto.mjs';

const decode = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
async function decrypt(envelope, password) {
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password),
    'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey({name: 'PBKDF2', hash: 'SHA-256',
    salt: decode(envelope.salt), iterations: envelope.iterations}, material,
    {name: 'AES-GCM', length: 256}, false, ['decrypt']);
  return new TextDecoder().decode(await crypto.subtle.decrypt(
    {name: 'AES-GCM', iv: decode(envelope.iv)}, key, decode(envelope.ciphertext)));
}

test('encrypted envelope roundtrips; wrong password and tampering fail', async () => {
  const secret = crypto.randomUUID();
  const password = crypto.randomUUID();
  const envelope = await encryptKey(secret, password);
  assert.equal(envelope.iterations, 310000);
  assert.equal(envelope.kdf, 'PBKDF2-SHA256');
  assert.equal(envelope.cipher, 'AES-256-GCM');
  assert.equal(await decrypt(envelope, password), secret);
  assert.ok(!JSON.stringify(envelope).includes(secret));
  await assert.rejects(decrypt(envelope, crypto.randomUUID()));
  const damaged = decode(envelope.ciphertext);
  damaged[0] ^= 1;
  await assert.rejects(decrypt({...envelope, ciphertext: btoa(String.fromCharCode(...damaged))}, password));
  const second = await encryptKey(secret, password);
  assert.notEqual(second.salt, envelope.salt);
  assert.notEqual(second.iv, envelope.iv);
  await assert.rejects(encryptKey(secret, 'short'));
});

test('panel issues key, clears credentials and downloads only encrypted data', async () => {
  const secret = crypto.randomUUID();
  const admin = crypto.randomUUID();
  const password = crypto.randomUUID();
  const fields = Object.fromEntries(['issue', 'gateway', 'admin', 'name', 'password',
    'confirm', 'submit', 'status', 'download'].map(id => [id, {value: ''}]));
  fields.gateway.value = 'https://gateway.example.com';
  fields.admin.value = admin;
  fields.name.value = 'panel-test';
  fields.password.value = fields.confirm.value = password;
  let submit;
  fields.issue.addEventListener = (_, handler) => { submit = handler; };
  globalThis.document = {getElementById: id => fields[id]};
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url.href, 'https://gateway.example.com/admin/api-keys');
    assert.equal(options.headers.Authorization, `Bearer ${admin}`);
    assert.equal(options.redirect, 'error');
    return {ok: true, json: async () => ({api_key: secret})};
  };
  try {
    await import('../pages/app.mjs');
    await submit({preventDefault() {}});
    assert.equal(fields.download.hidden, false);
    for (const id of ['admin', 'password', 'confirm']) assert.equal(fields[id].value, '');
    const envelope = await (await originalFetch(fields.download.href)).json();
    assert.equal(await decrypt(envelope, password), secret);
    assert.ok(!JSON.stringify(envelope).includes(admin));
    URL.revokeObjectURL(fields.download.href);
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.document;
  }
});
