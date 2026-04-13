#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF投手データ → pitcher_data.json 変換スクリプト

使い方:
  python convert_pitcher.py            # 差分確認のみ（JSONは変更しない）
  python convert_pitcher.py --update   # pitcher_data.json を更新する

対象ページ: P1・P2（Aチーム投手データ）
"""

import pdfplumber
import json
import re
import sys
import io
from pathlib import Path

# Windows コンソールの文字化け対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent

# 大会名の正規化
TOURNAMENT_MAP = {
    "あじさい":       "あじさいリーグ",
    "あじさいリーグ":  "あじさいリーグ",
    "区大会":         "区大会",
    "OP戦":          "OP戦",
    "さわやか":       "さわやかカップ",
    "さわやかカップ":  "さわやかカップ",
    "若獅子":         "若獅子カップ",
}

# 対戦相手名の正規化
OPPONENT_MAP = {
    "百合丘ペッカーズ": "百合ヶ丘ペッカーズ",
}

# スキップ対象行パターン
SKIP_PATTERNS = [
    r'^[A-Z]チーム投手データ',
    r'^a b c d',
    r'^日付\s+チーム',
    r'^回数',
    r'^同率',
    r'^c÷b',
]


# ────────────────────────────────────────────
# PDF検索
# ────────────────────────────────────────────
def find_pdf(team: str = 'A') -> Path:
    all_pdfs = sorted(BASE_DIR.glob("*.pdf"), key=lambda p: p.name, reverse=True)
    pdfs = [p for p in all_pdfs if p.name.startswith(team)]
    if not pdfs:
        raise FileNotFoundError(f"{team}チーム用PDFファイル（{team}データ分析★MMDD★.pdf）が見つかりません")
    if len(pdfs) > 1:
        print(f"複数のPDFが見つかりました。最新ファイルを使用します: {pdfs[0].name}")
    return pdfs[0]


# ────────────────────────────────────────────
# ヘッダー解析
# ────────────────────────────────────────────
def parse_game_header(line: str) -> dict | None:
    """
    例: "4月5日A あじさい 坂本少年野球部 負 2対12 計 105 44 42% ..."
    ヘッダー部分（日付〜スコア）のみ抽出する
    """
    # 相手名と勝敗の間にスペースがない場合（例: ヤンキース負）や
    # PDF抽出で勝敗文字の後に相手名の一部が残る場合（例: ヤンキー負ス）に対応
    m = re.match(
        r'(\d+)月(\d+)日([A-Z])\s+(\S+)\s+(.+?)\s*(勝|負|引き分け)(\S*)\s+(\d+対\d+)',
        line
    )
    if not m:
        return None
    month, day, team, tournament, opp_head, result, opp_tail, score = m.groups()
    opponent = (opp_head + opp_tail).strip()
    tournament = TOURNAMENT_MAP.get(tournament, tournament)
    opponent   = OPPONENT_MAP.get(opponent.strip(), opponent.strip())
    return {
        "date":       f"2026-{int(month):02d}-{int(day):02d}",
        "team":       team,
        "tournament": tournament,
        "opponent":   opponent,
        "result":     result,
        "score":      score,
    }


# ────────────────────────────────────────────
# 投手行のパース（数値列を抽出）
# ────────────────────────────────────────────
def parse_pitcher_tokens(tokens: list[str]) -> dict | None:
    """
    投手行フォーマット（%・#DIV/0! 等を除去して数値のみ取得）:
      【旧フォーマット 11数値】
        投球数 ストライク str率% 打者数 四死球 bb率% 奪三振 k率% 被安打 hit率%
        投球回 失点 飛球数 飛球アウト数 飛球アウト率% WP/PB
      【新フォーマット 12数値（エラー列追加）】
        ...同上... 飛球アウト率% エラー WP/PB

    Returns: 投手1人分のdictまたはNone
    """
    # %・#DIV/0! を除去し、数値と小数のみ残す
    nums = []
    for t in tokens:
        t = t.replace('#DIV/0!', '')
        if t.endswith('%'):
            continue
        # "率%ー" のような結合トークンの末尾 "ー" を除去
        t = t.rstrip('ー')
        try:
            nums.append(float(t))
        except ValueError:
            pass  # "ー" などは無視

    # 新フォーマット: 12個（エラー列あり）
    if len(nums) >= 12:
        return {
            "pitches":     int(nums[0]),
            "strikes":     int(nums[1]),
            "batters":     int(nums[2]),
            "walks":       int(nums[3]),
            "strikeouts":  int(nums[4]),
            "hits":        int(nums[5]),
            "innings":     nums[6],
            "runs":        int(nums[7]),
            "flyBalls":    int(nums[8]),
            "flyBallOuts": int(nums[9]),
            "errors":      int(nums[10]),
            "wp_pb":       int(nums[11]),
        }
    # 旧フォーマット: 11個（エラー列なし）
    elif len(nums) >= 11:
        return {
            "pitches":     int(nums[0]),
            "strikes":     int(nums[1]),
            "batters":     int(nums[2]),
            "walks":       int(nums[3]),
            "strikeouts":  int(nums[4]),
            "hits":        int(nums[5]),
            "innings":     nums[6],
            "runs":        int(nums[7]),
            "flyBalls":    int(nums[8]),
            "flyBallOuts": int(nums[9]),
            "wp_pb":       int(nums[10]),
        }
    else:
        return None


# ────────────────────────────────────────────
# 相手チーム投手行のパース
# ────────────────────────────────────────────
def parse_opponent_pitcher(tokens: list[str]) -> dict | None:
    """
    相手チーム投手計行:
      【旧フォーマット 8数値】
        ー ー ー 打者数 四死球 bb率% 奪三振 k率% 被安打 hit率% ー 失点 飛球数 飛球アウト数 飛球アウト率% WP/PB
      【新フォーマット 9数値（エラー列追加）】
        ...同上... 飛球アウト率% エラー WP/PB
    """
    nums = []
    for t in tokens:
        if t == 'ー' or t.endswith('%') or t == '#DIV/0!':
            continue
        t = t.rstrip('ー')
        try:
            nums.append(float(t))
        except ValueError:
            pass

    # 新フォーマット: 9個（エラー列あり）
    if len(nums) >= 9:
        return {
            "batters":     int(nums[0]),
            "walks":       int(nums[1]),
            "strikeouts":  int(nums[2]),
            "hits":        int(nums[3]),
            "runs":        int(nums[4]),
            "flyBalls":    int(nums[5]),
            "flyBallOuts": int(nums[6]),
            "errors":      int(nums[7]),
            "wp_pb":       int(nums[8]),
        }
    # 旧フォーマット: 8個（エラー列なし）
    elif len(nums) >= 8:
        return {
            "batters":     int(nums[0]),
            "walks":       int(nums[1]),
            "strikeouts":  int(nums[2]),
            "hits":        int(nums[3]),
            "runs":        int(nums[4]),
            "flyBalls":    int(nums[5]),
            "flyBallOuts": int(nums[6]),
            "wp_pb":       int(nums[7]),
        }
    else:
        return None


# ────────────────────────────────────────────
# PDFからの抽出
# ────────────────────────────────────────────
def extract_pitcher_games(pdf_path: Path, team: str = 'A') -> list[dict]:
    # Aチーム: P1+P2 (pages[0:2]) / Bチーム: P1のみ (pages[0:1])
    page_slice = slice(0, 1) if team != 'A' else slice(0, 2)
    min_pages  = 1 if team != 'A' else 2
    page_texts = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        if total < min_pages:
            raise ValueError(f"PDFのページ数が不足しています（{total}ページ）。")
        for page in pdf.pages[page_slice]:
            text = page.extract_text() or ""
            page_texts.append(text)

    full_text = "\n".join(page_texts)
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    games = []
    current_header = None
    current_pitchers = []
    opponent_pitchers = None

    def flush():
        """現在の試合データをgamesに追加"""
        if current_header and current_pitchers:
            games.append({
                **current_header,
                "pitchers": current_pitchers,
                "opponent_pitchers": opponent_pitchers or {},
            })

    for line in lines:
        # スキップ判定
        if any(re.match(p, line) for p in SKIP_PATTERNS):
            continue

        # 試合ヘッダー行（"4月5日A ..." のような行）
        if re.match(r'\d+月\d+日[A-Z]', line):
            flush()
            current_header = parse_game_header(line)
            current_pitchers = []
            opponent_pitchers = None
            if current_header is None:
                print(f"  [警告] ヘッダー解析失敗: {line}")
                continue

            # 同じ行に「計」の数値が続く場合があるので、「計」以降を投手行として処理
            # 例: "4月5日A あじさい 坂本少年野球部 負 2対12 計 105 44 42% ..."
            m = re.search(r'\s計\s+(.+)$', line)
            if m:
                tokens = m.group(1).split()
                stats = parse_pitcher_tokens(tokens)
                if stats:
                    current_pitchers.append({"name": "計", **stats})
            continue

        # 相手チーム投手計行
        if line.startswith("相手チーム投手計"):
            tokens = line.split()[1:]  # "相手チーム投手計" を除く
            opponent_pitchers = parse_opponent_pitcher(tokens)
            if opponent_pitchers is None:
                print(f"  [警告] 相手投手計解析失敗: {line}")
            continue

        # 投手個人行または「計」行（ヘッダーと同行でない場合）
        if current_header is None:
            continue

        tokens = line.split()
        if not tokens:
            continue

        # 投手名と数値の切り分け
        # 名前は漢字・スペース区切り（姓 名）で最大2トークン
        # 数値列の先頭は整数
        name_tokens = []
        val_tokens  = []
        for i, t in enumerate(tokens):
            try:
                float(t)
                val_tokens = tokens[i:]
                break
            except ValueError:
                if t.endswith('%') or t == 'ー' or t == '#DIV/0!':
                    val_tokens = tokens[i:]
                    break
                name_tokens.append(t)

        name = " ".join(name_tokens).strip()
        if not name or not val_tokens:
            continue

        # 「計」かつ既にヘッダー行で取得済みならスキップ
        if name == "計" and any(p["name"] == "計" for p in current_pitchers):
            continue

        stats = parse_pitcher_tokens(val_tokens)
        if stats:
            current_pitchers.append({"name": name, **stats})
        else:
            print(f"  [警告] 投手行解析失敗: {line}")

    # 最後の試合
    flush()

    return games


# ────────────────────────────────────────────
# バリデーション（選手合計 vs チーム合計）
# ────────────────────────────────────────────
def to_thirds(innings: float) -> int:
    """
    投球回数を1/3回単位の整数に変換。
    PDFの表記（3進数的小数）:
      X.0 = X回ちょうど → X*3
      X.3 = X回1/3     → X*3 + 1
      X.7 = X回2/3     → X*3 + 2
    """
    whole = int(innings)
    frac_str = f"{innings:.1f}".split('.')[1]  # "0", "3", "7"
    frac_map = {'0': 0, '3': 1, '7': 2}
    frac = frac_map.get(frac_str, round(float('0.' + frac_str) * 3))
    return whole * 3 + frac


def validate_game(game: dict) -> list[str]:
    warnings = []
    pitchers = [p for p in game["pitchers"] if p["name"] != "計"]
    totals   = next((p for p in game["pitchers"] if p["name"] == "計"), None)
    if not totals:
        warnings.append("    「計」行が見つかりません")
        return warnings

    for key in ["pitches", "strikes", "batters", "walks", "strikeouts", "hits", "runs", "wp_pb"]:
        computed = sum(p[key] for p in pitchers)
        expected = totals[key]
        if computed != expected:
            warnings.append(f"    {key}: チーム合計={expected}, 選手合計={computed} (差={computed - expected:+d})")

    # エラー列は新フォーマットのみ検証
    if "errors" in totals:
        computed = sum(p.get("errors", 0) for p in pitchers)
        expected = totals["errors"]
        if computed != expected:
            warnings.append(f"    errors: チーム合計={expected}, 選手合計={computed} (差={computed - expected:+d})")

    # 投球回数（3進数で比較）
    comp_inn = sum(to_thirds(p["innings"]) for p in pitchers)
    exp_inn  = to_thirds(totals["innings"])
    # 浮動小数点誤差を許容するため、1/3回（1サード）以内の差はOK
    if abs(comp_inn - exp_inn) > 1:
        warnings.append(f"    innings: チーム合計={totals['innings']}, 選手合計={sum(p['innings'] for p in pitchers):.1f}")

    return warnings


# ────────────────────────────────────────────
# 比較・レポート
# ────────────────────────────────────────────
def game_id(g: dict) -> str:
    return f"{g['date']}|{g.get('team','')}|{g['tournament']}|{g['opponent']}"


def compare_and_report(pdf_games: list, json_games: list):
    pdf_map  = {game_id(g): g for g in pdf_games}
    json_map = {game_id(g): g for g in json_games}
    pdf_keys  = set(pdf_map)
    json_keys = set(json_map)

    print(f"\n{'='*60}")
    print(f"PDF取得: {len(pdf_games)}試合  /  JSON既存: {len(json_games)}試合")

    only_pdf  = pdf_keys - json_keys
    only_json = json_keys - pdf_keys
    common    = pdf_keys & json_keys

    if only_pdf:
        print(f"\n【JSONに未追加の試合 (PDF → 新規追加候補)】")
        for k in sorted(only_pdf):
            print(f"  + {k}")

    if only_json:
        print(f"\n【PDFに存在しない試合 (JSONのみ)】")
        for k in sorted(only_json):
            print(f"  ? {k}")

    print(f"\n【共通試合の差分チェック ({len(common)}試合)】")
    total_diffs = 0
    for k in sorted(common):
        pg = pdf_map[k]
        jg = json_map[k]
        diffs = []

        pdf_pitcher_map  = {p["name"]: p for p in pg["pitchers"]}
        json_pitcher_map = {p["name"]: p for p in jg["pitchers"]}

        for name in set(pdf_pitcher_map) | set(json_pitcher_map):
            pp = pdf_pitcher_map.get(name)
            jp = json_pitcher_map.get(name)
            if pp is None:
                diffs.append(f"    [{name}] JSONにのみ存在")
                continue
            if jp is None:
                diffs.append(f"    [{name}] PDFにのみ存在")
                continue
            for key in ["pitches", "strikes", "batters", "walks", "strikeouts",
                        "hits", "innings", "runs", "flyBalls", "flyBallOuts", "errors", "wp_pb"]:
                pv = pp.get(key)
                jv = jp.get(key)
                if pv != jv:
                    diffs.append(f"    [{name}] {key}: PDF={pv}  JSON={jv}")

        if diffs:
            total_diffs += len(diffs)
            print(f"\n  試合: {k}")
            for d in diffs:
                print(d)

    if total_diffs == 0:
        print("  差分なし ✓")
    else:
        print(f"\n  合計 {total_diffs} 件の差分")

    print(f"\n【バリデーション: 選手合計 vs チーム合計 (PDF取得データ)】")
    validation_ok = True
    for g in pdf_games:
        warns = validate_game(g)
        if warns:
            validation_ok = False
            print(f"\n  ⚠ {game_id(g)}")
            for w in warns:
                print(w)
    if validation_ok:
        print("  全試合OK ✓")


# ────────────────────────────────────────────
# メイン
# ────────────────────────────────────────────
def main():
    update_mode = "--update" in sys.argv
    team = 'B' if '--team' in sys.argv and sys.argv[sys.argv.index('--team') + 1] == 'B' else 'A'

    print("PDFを検索中...")
    try:
        pdf_path = find_pdf(team)
        print(f"使用PDF: {pdf_path.name}")
    except FileNotFoundError as e:
        print(f"エラー: {e}")
        sys.exit(1)

    pages_desc = "P1" if team != 'A' else "P1・P2"
    print(f"投手データを抽出中 ({pages_desc})...")
    try:
        pdf_games = extract_pitcher_games(pdf_path, team)
    except Exception as e:
        print(f"抽出エラー: {e}")
        sys.exit(1)
    print(f"{len(pdf_games)} 試合を取得しました")

    pitcher_path = BASE_DIR / "pitcher_data.json"
    if pitcher_path.exists():
        with open(pitcher_path, encoding="utf-8") as f:
            json_games = json.load(f)
    else:
        json_games = []
        print("pitcher_data.json が存在しないため新規作成します")

    compare_and_report(pdf_games, json_games)

    if update_mode:
        # PDF優先でマージ（PDFにない試合はJSONから保持）
        merged = {game_id(g): g for g in json_games}
        for g in pdf_games:
            merged[game_id(g)] = g

        sorted_games = sorted(merged.values(), key=lambda g: g["date"], reverse=True)

        with open(pitcher_path, "w", encoding="utf-8") as f:
            json.dump(sorted_games, f, ensure_ascii=False, indent=2)
        print(f"\n✓ pitcher_data.json を更新しました ({len(sorted_games)} 試合)")
    else:
        print(f"\n※ 更新するには: python convert_pitcher.py --update")


if __name__ == "__main__":
    main()
