#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
종가배팅 후보 스크리너 — 키움증권 REST API
GitHub Actions에서 매 거래일 14:33 / 14:43 KST 자동 실행.

환경변수: KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_MOCK(선택, "1"이면 모의)
산출물: result.json, result.md
"""

import os
import re
import sys
import json
import time
import datetime as dt
import requests

MOCK = os.environ.get("KIWOOM_MOCK", "").strip() == "1"
BASE = "https://mockapi.kiwoom.com" if MOCK else "https://api.kiwoom.com"
APP_KEY = os.environ.get("KIWOOM_APP_KEY", "").strip()
APP_SECRET = os.environ.get("KIWOOM_APP_SECRET", "").strip()

MAX_PRICE = int(os.environ.get("MAX_PRICE", "500000"))
PREF_PRICE = int(os.environ.get("PREF_PRICE", "200000"))
TOP_N = int(os.environ.get("TOP_N", "25"))

KST = dt.timezone(dt.timedelta(hours=9))
RK = "/api/dostk/rkinfo"     # 순위정보
MKT = "/api/dostk/mrkcond"   # 시세
CHT = "/api/dostk/chart"     # 차트
STK = "/api/dostk/stkinfo"   # 종목정보

# ─────────────────────────────────────────────────────────────────────
# 제외종목 (키움 [대상변경] 설정과 동일하게 맞춤)
# ─────────────────────────────────────────────────────────────────────
# ka10099 종목정보리스트의 state/auditInfo/orderWarning 문자열에서 찾을 키워드
EXCLUDE_KEYWORDS = [
    "관리종목", "관리", "투자경고", "투자위험", "위험예고", "경고예고",
    "투자주의", "거래정지", "정리매매", "환기", "불성실공시",
    "증거금100", "증거금 100", "증100",
    "단기과열예고", "공매도과열", "이상급등", "초저유동성",
    "배당락", "배당낙", "권리락", "스팩", "SPAC",
]
# 체크 해제하신 항목 → 제외하지 않음
KEEP_KEYWORDS = ["담보대출불가", "단기과열지정", "대주가능"]

PREF_SUFFIX = re.compile(r"(우|우B|[0-9]우[B]?)$")          # 우선주
ETF_ETN_PAT = re.compile(
    r"(KODEX|TIGER|KBSTAR|ARIRANG|HANARO|KOSEF|SOL |ACE |PLUS |RISE |TIMEFOLIO"
    r"|마이티|파워|네비게이터|ETN|레버리지|인버스|선물\(|합성)", re.I)
SPAC_PAT = re.compile(r"(스팩|제\s*\d+\s*호)")

# ─────────────────────────────────────────────────────────────────────
# 거래원 특징 (사용자 자료 기준) — 세력주매매기법 보조 판정
# ─────────────────────────────────────────────────────────────────────
BROKER_CLASS = {
    "외국인": ["모간", "모건", "골드만", "메릴", "JP", "제이피", "씨지", "CLSA", "노무라",
               "다이와", "UBS", "크레디", "맥쿼리", "BNP", "도이치", "씨티", "HSBC",
               "미즈호", "SG", "바클레이", "제프리", "인스티넷"],
    "기관+외국인": ["미래에셋"],
    "투신": ["삼성증권", "한국증권", "한국투자", "신한"],
    "기타기관": ["하나", "신영"],
    "고액개인": ["NH투자", "엔에이치", "현대차증권", "현대증권", "대신"],
    "소액개인": ["키움", "유안타", "이베스트", "LS증권", "유진"],
}
SMART_MONEY = {"외국인", "기관+외국인", "투신", "기타기관"}
DUMB_MONEY = {"소액개인"}


def now_kst():
    return dt.datetime.now(KST)


def bail(status, msg, extra=None):
    out = {"status": status, "message": msg,
           "date": now_kst().strftime("%Y%m%d"),
           "time": now_kst().strftime("%H:%M:%S"), "candidates": []}
    if extra:
        out.update(extra)
    json.dump(out, open("result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    open("result.md", "w", encoding="utf-8").write(f"# {status}\n\n{msg}\n")
    print(f"[{status}] {msg}")
    sys.exit(0)


if not APP_KEY or not APP_SECRET:
    bail("error", "KIWOOM_APP_KEY / KIWOOM_APP_SECRET 시크릿이 설정되지 않았습니다.")


# ------------------------------------------------------------------ auth
def get_token():
    r = requests.post(f"{BASE}/oauth2/token",
                      headers={"Content-Type": "application/json;charset=UTF-8"},
                      json={"grant_type": "client_credentials",
                            "appkey": APP_KEY, "secretkey": APP_SECRET},
                      timeout=20)
    if r.status_code != 200:
        bail("error", f"토큰 발급 실패 HTTP {r.status_code}: {r.text[:300]}")
    j = r.json()
    tok = j.get("token") or j.get("access_token")
    if not tok:
        bail("error", f"토큰 응답에 token 없음: {str(j)[:300]}")
    return tok


try:
    TOKEN = get_token()
    print("토큰 발급 완료" + (" (모의투자)" if MOCK else " (실전)"))
except Exception as e:
    bail("error", f"토큰 발급 중 오류: {e}")


def call(resource, api_id, params, retries=3):
    h = {"Content-Type": "application/json;charset=UTF-8",
         "authorization": f"Bearer {TOKEN}", "api-id": api_id,
         "cont-yn": "N", "next-key": ""}
    for _ in range(retries):
        try:
            r = requests.post(f"{BASE}{resource}", headers=h, json=params, timeout=20)
            j = r.json()
            if j.get("return_code", 0) == 0:
                return j
            print(f"  [warn] {api_id} rc={j.get('return_code')} {j.get('return_msg')}")
            return j
        except Exception as e:
            print(f"  [warn] {api_id} {e}")
            time.sleep(0.8)
    return {}


def rows_of(resp):
    """응답에서 리스트형 데이터 키를 자동 탐지 (키움은 API마다 키 이름이 다름)."""
    if not resp:
        return []
    for k, v in resp.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def f(v, d=0.0):
    """키움 숫자는 '+1,500' '-0250' 형태로 옴."""
    if v is None:
        return d
    s = re.sub(r"[^\d.\-]", "", str(v).replace("+", ""))
    try:
        return float(s) if s not in ("", "-", ".") else d
    except Exception:
        return d


def g(row, *keys, default=""):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return default


# ------------------------------------------------------------- endpoints
def top_trading_value():
    """ka10032 거래대금상위 — 관리종목 제외(mang_stk_incls=0), 통합거래소."""
    return rows_of(call(RK, "ka10032",
                        {"mrkt_tp": "000", "mang_stk_incls": "0", "stex_tp": "3"}))


def top_change_rate():
    """ka10027 전일대비등락률상위 — 상승률순."""
    return rows_of(call(RK, "ka10027", {
        "mrkt_tp": "000", "sort_tp": "1", "trde_qty_cnd": "0000", "stk_cnd": "0",
        "crd_cnd": "0", "updown_incls": "1", "pric_cnd": "0",
        "trde_prica_cnd": "0", "stex_tp": "3"}))


def top_quote_balance():
    """ka10020 호가잔량상위 — 매수잔량 기준."""
    out = []
    for m in ("001", "101"):   # 코스피, 코스닥
        out += rows_of(call(RK, "ka10020", {
            "mrkt_tp": m, "sort_tp": "1", "trde_qty_tp": "0000",
            "stk_cnd": "0", "crd_cnd": "0", "stex_tp": "3"}))
        time.sleep(0.25)
    return out


def stock_info(code):
    """ka10001 주식기본정보."""
    j = call(STK, "ka10001", {"stk_cd": code})
    r = rows_of(j)
    if r:
        return r[0]
    return {k: v for k, v in j.items() if not isinstance(v, (list, dict))}


def daily_chart(code):
    """ka10081 주식일봉차트 (최신순)."""
    return rows_of(call(CHT, "ka10081", {
        "stk_cd": code, "base_dt": now_kst().strftime("%Y%m%d"),
        "upd_stkpc_tp": "1"}))


# ------------------------------------------------------- 제외종목 필터
def build_exclusion_map():
    """ka10099 종목정보리스트로 코드→상태문자열 맵을 만든다."""
    m = {}
    for mt in ("0", "10"):          # 0:코스피, 10:코스닥
        rows = rows_of(call(STK, "ka10099", {"mrkt_tp": mt}))
        for r in rows:
            cd = str(g(r, "code", "stk_cd")).strip()[:6]
            if not cd:
                continue
            blob = " ".join(str(g(r, k, default="")) for k in
                            ("state", "auditInfo", "orderWarning", "stk_stat",
                             "audit_info", "order_warning", "companyClassName"))
            m[cd] = blob
        print(f"  종목정보리스트 mrkt_tp={mt}: {len(rows)}건")
        time.sleep(0.3)
    return m


def exclusion_reason(code, name, excl_map):
    """제외 사유를 반환. 제외 대상이 아니면 None."""
    nm = (name or "").strip()

    if PREF_SUFFIX.search(nm):
        return "우선주"
    if ETF_ETN_PAT.search(nm):
        return "ETF/ETN"
    if SPAC_PAT.search(nm):
        return "스팩"
    # 우선주 코드 규칙: 6자리 중 끝자리가 5/7/9 또는 K/L/M
    if len(code) == 6 and (code[-1] in "579" and code[:1].isdigit()
                           and not code.endswith("0")):
        pass  # 코드만으로는 오탐이 많아 이름 규칙만 신뢰

    blob = excl_map.get(code, "")
    if blob:
        for kw in KEEP_KEYWORDS:
            blob = blob.replace(kw, "")
        for kw in EXCLUDE_KEYWORDS:
            if kw in blob:
                return kw
    return None


# --------------------------------------------------------- 거래원 분석
def classify_broker(nm):
    for cls, pats in BROKER_CLASS.items():
        for p in pats:
            if p in nm:
                return cls
    return "기타"


def broker_signal(code):
    """ka10040 당일주요거래원 — 매수/매도 상위 창구의 성격을 판정."""
    rows = rows_of(call(STK, "ka10040", {"stk_cd": code}))
    row = rows[0] if rows else {}
    buys, sells = [], []
    for i in range(1, 6):
        b = str(g(row, f"buy_trde_ori_nm_{i}", f"buy_trde_ori_nm{i}", default="")).strip()
        s = str(g(row, f"sel_trde_ori_nm_{i}", f"sel_trde_ori_nm{i}", default="")).strip()
        if b:
            buys.append(b)
        if s:
            sells.append(s)
    if not buys and not sells:
        return None
    return {"buy": [(b, classify_broker(b)) for b in buys],
            "sell": [(s, classify_broker(s)) for s in sells]}


# --------------------------------------------------------------- scoring
def analyse(code, name, ul_sectors, quote_codes, seed):
    d = stock_info(code)
    price = abs(f(g(d, "cur_prc", "stk_prpr", default=seed.get("cur_prc"))))
    if price <= 0 or price > MAX_PRICE:
        return None

    chg = f(g(d, "flu_rt", "prdy_ctrt", default=seed.get("flu_rt")))
    vol = abs(f(g(d, "trde_qty", default=seed.get("trde_qty"))))
    amount = abs(f(g(d, "trde_prica", default=seed.get("trde_prica"))))
    # 키움 거래대금은 보통 '백만원' 단위 → 원으로 환산
    amount_won = amount * 1e6 if amount and amount < 1e7 else amount
    op = abs(f(g(d, "open_pric", "opn_pric")))
    hi = abs(f(g(d, "high_pric", "hgst_pric")))
    lo = abs(f(g(d, "low_pric", "lwst_pric")))
    sector = str(g(d, "upName", "upname", "induty_nm", "bstp_kor_isnm")).strip()

    rows = daily_chart(code)
    closes = [abs(f(g(r, "cur_prc", "stk_clpr"))) for r in rows]
    vols = [abs(f(g(r, "trde_qty"))) for r in rows]
    highs = [abs(f(g(r, "high_pric", "hgst_pric"))) for r in rows]
    closes = [c for c in closes if c > 0]
    ma5 = sum(closes[:5]) / 5 if len(closes) >= 5 else None
    ma20 = sum(closes[:20]) / 20 if len(closes) >= 20 else None
    ma60 = sum(closes[:60]) / 60 if len(closes) >= 60 else None
    prev_vol = vols[1] if len(vols) > 1 else 0
    box_high = max(highs[1:21]) if len(highs) > 21 else None
    hist_high = max(highs[1:]) if len(highs) > 1 else None
    if not op and rows:
        op = abs(f(g(rows[0], "open_pric")))
        hi = hi or abs(f(g(rows[0], "high_pric")))
        lo = lo or abs(f(g(rows[0], "low_pric")))

    score, reasons, flags = 0, [], []

    if amount_won >= 3000e8:
        score += 3; reasons.append(f"거래대금 {amount_won/1e8:,.0f}억(초대형)")
    elif amount_won >= 1000e8:
        score += 3; reasons.append(f"거래대금 {amount_won/1e8:,.0f}억(1000억↑)")
    elif amount_won >= 300e8:
        score += 1; reasons.append(f"거래대금 {amount_won/1e8:,.0f}억")
    else:
        score -= 2; flags.append(f"거래대금 {amount_won/1e8:,.0f}억(얇음)")

    vmul = (vol / prev_vol * 100) if prev_vol else 0
    if vmul >= 500:
        score += 3; reasons.append(f"거래량 전일比 {vmul:,.0f}%(500%↑)")
    elif vmul >= 300:
        score += 2; reasons.append(f"거래량 전일比 {vmul:,.0f}%")
    elif vmul >= 150:
        score += 1; reasons.append(f"거래량 전일比 {vmul:,.0f}%")

    rng = hi - lo
    pos = ((price - lo) / rng * 100) if rng > 0 else 100
    if pos >= 90:
        score += 3; reasons.append(f"고가권 마감(저점 지지, 위치 {pos:.0f}%)")
    elif pos >= 75:
        score += 2; reasons.append(f"강한 흐름(종가위치 {pos:.0f}%)")
    elif pos < 40:
        score -= 2; flags.append(f"장중 밀림(종가위치 {pos:.0f}%)")

    if op > 0:
        body = (price - op) / op * 100
        tail = ((hi - price) / price * 100) if price else 0
        if body >= 5:
            score += 2; reasons.append(f"장대양봉(몸통 +{body:.1f}%)")
        elif body > 0:
            score += 1
        if tail <= 1.0 and chg > 0:
            score += 1; reasons.append("윗꼬리 거의 없음")
        elif tail >= 5:
            score -= 1; flags.append(f"윗꼬리 {tail:.1f}%")

    if ma5 and ma20:
        if price > ma5 > ma20 and (not ma60 or ma20 > ma60):
            score += 2; reasons.append("정배열(주가>5일>20일)")
        elif price > ma5 and price > ma20:
            score += 1; reasons.append("5·20일선 위")
        elif price < ma20:
            score -= 2; flags.append("20일선 아래")

    if box_high and price > box_high:
        score += 3; reasons.append(f"20일 박스 돌파(전고 {box_high:,.0f}원)")
    if hist_high and price >= hist_high:
        score += 2; reasons.append("신고가")
    if sector and sector in ul_sectors:
        score += 2; reasons.append(f"오늘 상한가 나온 섹터({sector})")
    if code in quote_codes:
        score += 1; reasons.append("호가잔량 상위")

    if chg >= 29:
        score += 1; flags.append(f"상한가(+{chg:.1f}%) — 상따 판단 필요")
    elif 8 <= chg <= 25:
        score += 2; reasons.append(f"당일 +{chg:.1f}%")
    elif chg > 25:
        score += 1; flags.append(f"당일 +{chg:.1f}%(과열 주의)")
    elif chg < 0:
        score -= 3; flags.append(f"당일 {chg:.1f}%(음봉)")

    if price < PREF_PRICE:
        score += 2; reasons.append(f"{price:,.0f}원(20만원 미만)")
    else:
        reasons.append(f"{price:,.0f}원")

    # ── 거래원 특징 (세력주매매기법)
    bs = broker_signal(code)
    brokers = None
    if bs:
        buy_cls = [c for _n, c in bs["buy"]]
        sell_cls = [c for _n, c in bs["sell"]]
        top_buy = bs["buy"][0] if bs["buy"] else None
        if any(c in SMART_MONEY for c in buy_cls[:3]):
            smart = [n for n, c in bs["buy"][:3] if c in SMART_MONEY]
            score += 2; reasons.append(f"매수창구에 기관·외국인({'/'.join(smart)})")
        if top_buy and top_buy[1] in DUMB_MONEY:
            score -= 2
            flags.append(f"매수 1위가 소액개인 창구({top_buy[0]}) — 반대 확률 높음")
        if any(c in DUMB_MONEY for c in sell_cls[:2]):
            score += 1; reasons.append("소액개인 창구 매도 상위")
        if any(c in SMART_MONEY for c in sell_cls[:2]):
            score -= 1
            flags.append("기관·외국인 창구가 매도 상위")
        brokers = {"buy": [f"{n}({c})" for n, c in bs["buy"]],
                   "sell": [f"{n}({c})" for n, c in bs["sell"]]}

    return {"code": code, "name": name, "sector": sector, "price": price,
            "chg": round(chg, 2), "amount_eok": round(amount_won / 1e8),
            "vol_mult": round(vmul), "close_pos": round(pos),
            "ma5": round(ma5) if ma5 else None, "ma20": round(ma20) if ma20 else None,
            "box_high": round(box_high) if box_high else None,
            "brokers": brokers,
            "score": score, "reasons": reasons, "flags": flags}


# ------------------------------------------------------------------ main
def main():
    today = now_kst().strftime("%Y%m%d")
    if now_kst().weekday() >= 5:
        bail("holiday", f"{today}은 주말입니다.")

    print(f"# 기준 {today} {now_kst():%H:%M:%S} KST")

    gainers = top_change_rate()
    if not gainers:
        bail("error", "등락률상위 조회 실패 — API 응답이 비었습니다. "
                      "앱키 권한 또는 파라미터를 확인하세요.")

    # 상한가(+29% 이상) 종목의 섹터 추출
    ul, ul_sectors = [], []
    for r in gainers[:40]:
        if f(g(r, "flu_rt")) >= 29:
            nm = str(g(r, "stk_nm", "hts_kor_isnm")).strip()
            cd = str(g(r, "stk_cd", "mksc_shrn_iscd")).strip()[:6]
            if nm:
                ul.append(nm)
            if cd and len(ul_sectors) < 8:
                s = str(g(stock_info(cd), "upName", "upname", "induty_nm")).strip()
                if s:
                    ul_sectors.append(s)
                time.sleep(0.2)
    ul_sectors = list(dict.fromkeys(ul_sectors))
    print(f"# 상한가 {len(ul)}종목: {', '.join(ul) or '-'}")
    print(f"# 상한가 섹터: {', '.join(ul_sectors) or '-'}")

    quote_codes = {str(g(r, "stk_cd")).strip()[:6] for r in top_quote_balance()}

    print("# 제외종목 목록 구성 중...")
    excl_map = build_exclusion_map()

    uni = top_trading_value()
    if not uni:
        bail("error", "거래대금상위 조회 실패 — API 응답이 비었습니다.")
    print(f"# 거래대금 상위 {len(uni)}종목 수신")

    seen, results, excluded = set(), [], []
    for r in uni:
        if len(results) >= TOP_N:
            break
        code = str(g(r, "stk_cd", "mksc_shrn_iscd")).strip()[:6]
        name = str(g(r, "stk_nm", "hts_kor_isnm")).strip()
        if not code or code in seen:
            continue
        seen.add(code)

        why = exclusion_reason(code, name, excl_map)
        if why:
            excluded.append(f"{name}({why})")
            continue

        try:
            a = analyse(code, name, ul_sectors, quote_codes, r)
            if a:
                results.append(a)
        except Exception as e:
            print(f"  [skip] {name}({code}) {e}")
        time.sleep(0.25)

    if excluded:
        print(f"# 제외 {len(excluded)}종목: {', '.join(excluded[:20])}")

    if not results:
        bail("error", f"가격 {MAX_PRICE:,}원 미만 조건을 통과한 종목이 없습니다.")

    results.sort(key=lambda x: -x["score"])

    lines = [f"# 종가배팅 후보 — {today} {now_kst():%H:%M} KST", ""]
    if ul:
        lines += [f"**오늘 상한가({len(ul)})**: {', '.join(ul)}",
                  f"**상한가 섹터**: {', '.join(ul_sectors) or '-'}", ""]
    lines.append("## 스코어 상위")
    for i, a in enumerate(results[:10], 1):
        lines.append(f"{i}. **{a['name']}**({a['code']}) `{a['score']:+d}점` — "
                     f"{a['price']:,.0f}원 {a['chg']:+.2f}% / 거래대금 {a['amount_eok']:,}억 / "
                     f"종가위치 {a['close_pos']}% / [{a['sector']}]")
        lines.append(f"   - ✅ {' · '.join(a['reasons'])}")
        if a["flags"]:
            lines.append(f"   - ⚠️ {' · '.join(a['flags'])}")
        if a.get("brokers"):
            lines.append(f"   - 매수창구 {' / '.join(a['brokers']['buy'][:3])}")
            lines.append(f"   - 매도창구 {' / '.join(a['brokers']['sell'][:3])}")
    if excluded:
        lines += ["", f"**제외 {len(excluded)}종목**: {', '.join(excluded[:20])}"]
    md = "\n".join(lines)
    print("\n" + md)

    json.dump({"status": "ok", "date": today,
               "time": now_kst().strftime("%H:%M:%S"),
               "upper_limit": ul, "upper_limit_sectors": ul_sectors,
               "excluded": excluded, "candidates": results[:10]},
              open("result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    open("result.md", "w", encoding="utf-8").write(md + "\n")


if __name__ == "__main__":
    main()