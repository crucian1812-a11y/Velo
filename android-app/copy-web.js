// Копирует веб-приложение из корня репозитория в www/ для сборки Android-приложения.
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const out = path.join(__dirname, 'www');
const items = ['index.html', 'manifest.webmanifest', 'icon.svg', 'sw.js', 'css', 'js'];

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });
for (const item of items) {
  fs.cpSync(path.join(root, item), path.join(out, item), { recursive: true });
}
console.log('Скопировано в', out);
