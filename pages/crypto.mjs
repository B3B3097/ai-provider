// Only the encrypted envelope may leave browser memory.
const encode = value => btoa(String.fromCharCode(...value));
export async function encryptKey(apiKey, password) {
  if (!globalThis.crypto?.subtle) throw new Error('Use HTTPS or localhost.');
  if (password.length < 12) throw new Error('Use a password of at least 12 characters.');
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const material = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey(
    {name: 'PBKDF2', hash: 'SHA-256', salt, iterations: 310000},
    material, {name: 'AES-GCM', length: 256}, false, ['encrypt']);
  const ciphertext = await crypto.subtle.encrypt(
    {name: 'AES-GCM', iv}, key, new TextEncoder().encode(apiKey));
  return {version: 1, cipher: 'AES-256-GCM', kdf: 'PBKDF2-SHA256',
    iterations: 310000, salt: encode(salt), iv: encode(iv),
    ciphertext: encode(new Uint8Array(ciphertext))};
}
