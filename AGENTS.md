# syuron 作業メモ（Claude Code・Codex共通）

## 週次進捗の記録

毎週の進捗報告は `weekly_reports/` に記録する。詳しくは `weekly_reports/README.md`。

- 研究・実装で実質的な作業をしたら、`weekly_reports/` の最新の `YYYYMMDD.md`（次の報告日）に
  該当する節（今週やったこと／結果／課題・相談したいこと／来週やること）を短く追記する。
  ファイルがなければ `weekly_reports/TEMPLATE.md` をコピーして作る。
- `ppflow_change_automatic/docs/progress/` を更新する作業では、そちらの更新を優先し、
  週次記録には要点と図のパスだけを書く。
- 確認していない結果を完了扱いで書かない。仮説には【仮】を付ける。

## 週次pptxの作り方

「`weekly_reports/YYYYMMDD.md` からpptxを作って」と頼まれたら、

1. pptxスキルを使い、`進捗.pptx` をスタイルの見本として読む（16:9、Officeテーマ、游ゴシック、左上32ptタイトル）。
2. `###` 見出し1つをスライド1枚にし、`weekly_reports/README.md` の「スライドの体裁」に従う。
3. Markdown中の図を貼り、出典URLを載せる。
4. `weekly_reports/pptx/YYYYMMDD_進捗報告.pptx` に保存し、画像に書き出して見た目を確認する。

## 共同作業

Claude Code と Codex は交代で作業する。作業前に `git status` を確認し、
他方の未コミットの変更があるファイルには触れない。引き継ぎ時にコミットする。
