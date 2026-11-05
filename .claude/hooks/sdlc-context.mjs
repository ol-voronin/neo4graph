// Згенеровано office/scripts/sync-sdlc.mjs з plugins/sdlc/hooks/context.mjs. Не редагуй тут.
// Економія контексту. Один скрипт на три події:
//  UserPromptSubmit — кожні N ходів нагадує моделі записати стан у .sdlc/notes.md і запропонувати /compact.
//  PreCompact       — каже, що саме зберегти в стислому підсумку, а що викинути (виводи команд, прочитані файли).
//  SessionStart (compact|resume) — повертає в контекст .sdlc/notes.md, щоб після стиснення не перечитувати репо.
// Усе, що скрипт друкує в stdout, потрапляє в контекст як системне нагадування; помилки не ламають сесію (exit 0).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const EVERY = Number(process.env.SDLC_NOTE_EVERY || 25);
let raw = '';
process.stdin.on('data', (c) => (raw += c));
process.stdin.on('end', () => {
  let i; try { i = JSON.parse(raw); } catch { process.exit(0); }
  const ev = i.hook_event_name, cwd = i.cwd || process.cwd();
  const notes = path.join(cwd, '.sdlc', 'notes.md');
  try {
    if (ev === 'UserPromptSubmit') {
      const f = path.join(os.tmpdir(), `sdlc-${String(i.session_id || 'x').replace(/\W/g, '')}.n`);
      const n = (Number(fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : 0) || 0) + 1;
      fs.writeFileSync(f, String(n));
      if (n % EVERY === 0) process.stdout.write(`[sdlc] Хід ${n}. Запиши поточний стан у .sdlc/notes.md (до 10 рядків: задача, етап, рішення власника, відкриті питання, що далі), не переписуючи решту файлу, і одним рядком запропонуй власнику /compact, якщо контекст великий.\n`);
    } else if (ev === 'PreCompact') {
      process.stdout.write('Стискай так: збережи дослівно рішення власника, поточну спеку і етап SDLC, список змінених файлів, відкриті гейти й питання, адреси PR і превʼю. Викинь виводи команд, вміст прочитаних файлів, проміжні варіанти й скриншоти. Якщо є .sdlc/notes.md — онови його перед стисненням.\n');
    } else if (ev === 'SessionStart' && (i.source === 'compact' || i.source === 'resume') && fs.existsSync(notes)) {
      const t = fs.readFileSync(notes, 'utf8').trim().slice(0, 4000);
      if (t) process.stdout.write(`[sdlc] Стан із .sdlc/notes.md (не перечитуй репо без потреби):\n${t}\n`);
    }
  } catch {}
  process.exit(0);
});
