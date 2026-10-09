import {encryptKey} from './crypto.mjs';
const field = id => document.getElementById(id);
let downloadUrl;
field('issue').addEventListener('submit', async event => {
  event.preventDefault();
  field('status').textContent = '';
  const password = field('password').value;
  if (password !== field('confirm').value) {
    field('status').textContent = 'Пароли не совпадают.';
    return;
  }
  let base;
  try {
    base = new URL(field('gateway').value);
    if (base.username || base.password || base.search || base.hash || base.pathname !== '/' ||
        !(base.protocol === 'https:' || (base.protocol === 'http:' &&
        ['localhost', '127.0.0.1'].includes(base.hostname)))) throw new Error();
    // Check Web Crypto before issuing an irreversible new key.
    if (!crypto.subtle || password.length < 12) throw new Error();
  } catch {
    field('status').textContent = 'Нужен HTTPS origin (или localhost), Web Crypto и пароль от 12 символов.';
    return;
  }
  field('submit').disabled = true;
  field('download').hidden = true;
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  let issued = false;
  let rawKey;
  try {
    const request = fetch(new URL('/admin/api-keys', base), {
      method: 'POST', redirect: 'error', credentials: 'omit', cache: 'no-store',
      headers: {'Content-Type': 'application/json', Authorization: `Bearer ${field('admin').value}`},
      body: JSON.stringify({name: field('name').value}),
    });
    field('admin').value = '';
    const response = await request;
    if (!response.ok) throw new Error();
    issued = true;
    const payload = await response.json();
    rawKey = payload.api_key;
    delete payload.api_key;
    if (typeof rawKey !== 'string' || !rawKey) throw new Error();
    const envelope = await encryptKey(rawKey, password);
    downloadUrl = URL.createObjectURL(new Blob([JSON.stringify(envelope, null, 2)], {type: 'application/json'}));
    field('download').href = downloadUrl;
    field('download').hidden = false;
    field('status').textContent = 'Ключ выпущен и зашифрован. Скачайте файл перед закрытием страницы.';
  } catch {
    field('status').textContent = issued
      ? 'Ключ выпущен, но шифрование не завершено. Отзовите его через админ-API перед повторной попыткой.'
      : 'Запрос не завершён. Проверьте адрес, ключ, CORS и список ключей перед повторной попыткой.';
  } finally {
    rawKey = undefined;
    for (const id of ['admin', 'password', 'confirm']) field(id).value = '';
    field('submit').disabled = false;
  }
});
