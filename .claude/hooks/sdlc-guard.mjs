// Згенеровано office/scripts/sync-sdlc.mjs з plugins/sdlc/hooks/guard.mjs. Не редагуй тут.
// Запобіжник sdlc. Працює перед кожним Bash/Edit/Write.
// deny  — секрети: .env*, *.pem, *.key, .git-credentials.
// ask   — прод: push у main/master, force-push, vercel --prod / promote. Питає людину, а не забороняє.
// Помилка розбору — пропускаємо (exit 0): запобіжник не має ламати звичайну роботу.
let raw = '';
process.stdin.on('data', (c) => (raw += c));
process.stdin.on('end', () => {
  let input;
  try { input = JSON.parse(raw); } catch { process.exit(0); }
  const tool = input.tool_name || '';
  const ti = input.tool_input || {};
  const decide = (decision, reason) => {
    process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: decision, permissionDecisionReason: reason } }));
    process.exit(0);
  };
  const SECRET = /(^|[\/\\])(\.env(\.[^\/\\]*)?|[^\/\\]+\.(pem|key)|\.git-credentials)$/i;

  if (tool !== 'Bash') {
    const p = ti.file_path || ti.notebook_path || '';
    if (SECRET.test(p) && !/\.env\.example$/i.test(p)) decide('deny', `sdlc: файл із секретами (${p}) не редагуємо. Секрети — лише в env Vercel.`);
    process.exit(0);
  }

  const cmd = String(ti.command || '');
  if (/\b(cat|less|more|head|tail|cp|scp)\b[^|;&]*(\.env\b|\.git-credentials|\.pem\b|id_rsa)/.test(cmd)) decide('deny', 'sdlc: не читаємо й не копіюємо файли із секретами.');
  if (/\bgit\s+push\b[^;&|]*(\s--force\b|\s-f\b|\s--force-with-lease\b)/.test(cmd)) decide('ask', 'sdlc: force-push переписує історію. Підтверди сам.');
  if (/\bgit\s+push\b[^;&|]*(\s|:)(main|master)\b/.test(cmd)) decide('ask', 'sdlc: push у main = прод. Підтверди сам або злий PR на GitHub.');
  if (/\bvercel\b[^;&|]*(--prod\b|\bpromote\b)/.test(cmd)) decide('ask', 'sdlc: це деплой у прод. Підтверди сам.');
  if (/\b(gh\s+pr\s+merge)\b/.test(cmd)) decide('ask', 'sdlc: злиття PR = реліз. Підтверди сам.');
  process.exit(0);
});
