"""Test 3: LLM threat labels (Haiku batch sample ~20% of rule-flagged panel posts + Gemini) vs missile days in Kharkiv.
Same protocol as test 2: expanding-window one-step logistic, baseline = own lags; signal = yesterday's feature; block-bootstrap CI."""
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.preprocessing import StandardScaler
RNG=np.random.default_rng(7)
d=pd.read_csv('data/daily_rhetoric.csv'); l=pd.read_csv('/tmp/claude-0/-home-claude/bf811a2d-7476-58b6-8f9f-2eb8f70b202b/scratchpad/llm_daily.csv'); l.columns=['date']+['L_'+c for c in l.columns[1:]]
df=d.merge(l,on='date',how='left').sort_values('date').reset_index(drop=True)
df['missile_day']=(df['missiles']>0).astype(int)
# LLM features: thr_ru is the daily share of rule-flagged posts; the LLM sample estimates what fraction of those are real threats
n=df['L_n_ru'].replace(0,np.nan)
df['p2_ru']=(df['L_t2_ru']/n)          # share of rule-flagged posts the LLM confirms as threat>=2 (precision of the rule that day)
df['llm_thr_ru']=df['thr_ru']*df['p2_ru']   # estimated share of all RU posts that are LLM-confirmed threats
df['llm_l2_ru']=df['thr_ru']*df['L_l2_ru']/n   # ... threat>=2 with target or timeframe, not scheduled context
df['llm_t3_ru']=df['thr_ru']*df['L_t3_ru']/n
df['llm_rh_ru']=df['thr_ru']*df['L_rh_ru']/n   # rhetoric (not operational) share
cov=df['L_n_ru'].notna().mean(); print(f"days {len(df)}, days with LLM labels {df['L_n_ru'].notna().sum()} ({cov:.0%}), missile days {df['missile_day'].sum()}")
for c in ['p2_ru','llm_thr_ru','llm_l2_ru','llm_t3_ru','llm_rh_ru']: df[c]=df[c].fillna(df[c].median())  # days without labels: median (reported)
def feats(sig):
    y=df['missile_day']; F=pd.DataFrame({'y1':y.shift(1),'y3':y.shift(1).rolling(3).mean(),'y7':y.shift(1).rolling(7).mean(),'i1':df['idx'].shift(1)})
    if sig: F['s1']=df[sig].shift(1); F['s3']=df[sig].shift(1).rolling(3).mean()
    return F
def oos(sig,min_train=30):
    F=feats(sig); y=df['missile_day'].values; p=np.full(len(df),np.nan)
    for t in range(min_train,len(df)):
        tr=F.iloc[:t].dropna(); idx=tr.index
        if len(set(y[idx]))<2 or F.iloc[t].isna().any(): continue
        sc=StandardScaler().fit(tr); m=LogisticRegression(C=1.0,max_iter=500).fit(sc.transform(tr),y[idx])
        p[t]=m.predict_proba(sc.transform(F.iloc[[t]]))[0,1]
    return p
def auc(p,y,mask): return roc_auc_score(y[mask],p[mask])
y=df['missile_day'].values; pb=oos(None); mb=~np.isnan(pb)
print(f"baseline AUC {auc(pb,y,mb):.3f}  Brier {brier_score_loss(y[mb],pb[mb]):.3f}  (n_test {mb.sum()}, positives {y[mb].sum()})")
def block_ci(pa,pb,y,mask,B=600,L=10):
    ii=np.where(mask)[0]; n=len(ii); out=[]
    for _ in range(B):
        starts=RNG.integers(0,n-L+1,size=int(np.ceil(n/L))); bi=np.concatenate([ii[s:s+L] for s in starts])[:n]
        if len(set(y[bi]))<2: continue
        out.append(roc_auc_score(y[bi],pa[bi])-roc_auc_score(y[bi],pb[bi]))
    return np.percentile(out,[2.5,97.5])
half=len(df)//2
for sig in ['thr_ru','l2_ru','p2_ru','llm_thr_ru','llm_l2_ru','llm_t3_ru','llm_rh_ru']:
    if sig not in df: continue
    pa=oos(sig); m=mb&~np.isnan(pa); lo,hi=block_ci(pa,pb,y,m)
    h1=m.copy();h1[half:]=False; h2=m.copy();h2[:half]=False
    print(f"{sig:12s} AUC {auc(pa,y,m):.3f} (Δ {auc(pa,y,m)-auc(pb,y,m):+.3f} [{lo:+.3f},{hi:+.3f}])  Brier {brier_score_loss(y[m],pa[m]):.3f}  halves base {auc(pb,y,h1):.2f}/{auc(pb,y,h2):.2f} full {auc(pa,y,h1):.2f}/{auc(pa,y,h2):.2f}")
# simple lag-1 correlations for orientation
for sig in ['thr_ru','p2_ru','llm_thr_ru','llm_l2_ru','llm_t3_ru']:
    print(sig,'corr(sig[t-1], missile_day[t]) = %+.3f'%np.corrcoef(df[sig].shift(1).dropna(), df['missile_day'][1:])[0,1], ' reverse %+.3f'%np.corrcoef(df['missile_day'].shift(1).dropna(), df[sig][1:])[0,1])
