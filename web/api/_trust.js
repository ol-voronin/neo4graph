// Trust score from resolved statements (docs/factcheck.md "Рівень довіри"). Each resolved statement scores y = 1 (true),
// 0.5 (partial), 0 (false); 'unresolvable' and 'pending' do not count, nor do demands (not true/false by nature).
// Hierarchical Beta shrinkage: all statements -> country -> group -> person. A level's prior is Beta(K·m, K·(1−m)) with
// m = the posterior mean of the level above, so a person with 2 resolved statements sits near their group's mean and
// moves away only as evidence piles up. Score = posterior mean; interval = 10%..90% Beta quantiles (80%).
export const K = 8;          // prior strength, in pseudo-statements
export const MIN_N = 5;      // below this many resolved statements: «недостатньо даних»
export const SCORED = ["claim_fact", "denial", "forecast", "promise", "threat", "announce_action"];

function lgamma(x) {
  const c = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059,
    12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7];
  if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
  x -= 1;
  let a = c[0];
  const t = x + 7.5;
  for (let i = 1; i < 9; i++) a += c[i] / (x + i);
  return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
}
function betacf(x, a, b) {
  const F = 1e-300, qab = a + b, qap = a + 1, qam = a - 1;
  let c = 1, d = 1 - (qab * x) / qap;
  if (Math.abs(d) < F) d = F;
  d = 1 / d;
  let h = d;
  for (let m = 1; m <= 300; m++) {
    const m2 = 2 * m;
    let aa = (m * (b - m) * x) / ((qam + m2) * (a + m2));
    d = 1 + aa * d; if (Math.abs(d) < F) d = F; c = 1 + aa / c; if (Math.abs(c) < F) c = F; d = 1 / d; h *= d * c;
    aa = (-(a + m) * (qab + m) * x) / ((a + m2) * (qap + m2));
    d = 1 + aa * d; if (Math.abs(d) < F) d = F; c = 1 + aa / c; if (Math.abs(c) < F) c = F; d = 1 / d;
    const del = d * c; h *= del;
    if (Math.abs(del - 1) < 3e-12) break;
  }
  return h;
}
export function ibeta(x, a, b) {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  const bt = Math.exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * Math.log(x) + b * Math.log(1 - x));
  return x < (a + 1) / (a + b + 2) ? (bt * betacf(x, a, b)) / a : 1 - (bt * betacf(1 - x, b, a)) / b;
}
export function betaQ(p, a, b) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 50; i++) { const m = (lo + hi) / 2; if (ibeta(m, a, b) < p) lo = m; else hi = m; }
  return (lo + hi) / 2;
}
const r3 = (x) => Math.round(x * 1000) / 1000;

// c: {t, f, p, u, q} counts (true, false, partial, unresolvable, pending); prior: mean of the level above
export function score(c, prior, k = K) {
  const n = c.t + c.f + c.p, S = c.t + 0.5 * c.p;
  const a = k * prior + S, b = k * (1 - prior) + n - S;
  return { n, true: c.t, false: c.f, partial: c.p, unresolvable: c.u || 0, pending: c.q || 0,
    raw: n ? r3(S / n) : null, mean: r3(a / (a + b)), lo: r3(betaQ(0.1, a, b)), hi: r3(betaQ(0.9, a, b)), prior: r3(prior), enough: n >= MIN_N };
}
const add = (x, y) => { for (const k of ["t", "f", "p", "u", "q", "all"]) x[k] = (x[k] || 0) + (y[k] || 0); return x; };

// rows: one per (country, grp, actor_id) with counts -> scored tree. Groups and countries pool their statements (each
// statement weighs 1), then shrink toward the level above.
export function hierarchy(rows) {
  const G = {}, C = {}, P = {}, top = {};
  for (const r of rows) {
    add(top, r);
    add((C[r.country] ??= { id: r.country }), r);
    add((G[r.grp] ??= { id: r.grp, country: r.country, _c: {} }), r);
    G[r.grp]._c[r.country] = (G[r.grp]._c[r.country] || 0) + r.all;
    if (r.actor_id) {
      const p = (P[r.actor_id] ??= { id: r.actor_id, label: r.label, _g: {} });
      add(p, r);
      p._g[r.grp] = (p._g[r.grp] || 0) + r.all;
    }
  }
  const all = score({ t: top.t || 0, f: top.f || 0, p: top.p || 0, u: top.u, q: top.q }, 0.5, 2); // Beta(1,1) at the top
  for (const c of Object.values(C)) c.score = score(c, all.mean);
  for (const g of Object.values(G)) {
    g.country = Object.entries(g._c).sort((a, b) => b[1] - a[1])[0][0];
    g.score = score(g, C[g.country]?.score.mean ?? all.mean);
  }
  for (const p of Object.values(P)) {
    p.grp = Object.entries(p._g).sort((a, b) => b[1] - a[1])[0][0];
    p.country = G[p.grp].country;
    p.score = score(p, G[p.grp].score.mean);
  }
  const clean = (o) => { const { _c, _g, t, f, p, u, q, ...x } = o; return x; };
  return { all, countries: Object.values(C).map(clean), groups: Object.values(G).map(clean), people: Object.values(P).map(clean) };
}

// monthly rows {m, t, f, p} -> cumulative score after each month (how the estimate evolved), same prior
export function trend(months, prior) {
  const acc = { t: 0, f: 0, p: 0 };
  return months.map((r) => { add(acc, r); const s = score(acc, prior); return { m: r.m, n: s.n, mean: s.mean, lo: s.lo, hi: s.hi, new: r.t + r.f + r.p }; });
}
