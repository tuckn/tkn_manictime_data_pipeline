# PC利用レポート

`build-report` は取得済みCSVからローカルHTMLを生成します。入口は一つの `index.html` です。
年・月・週のフィルターと同じ場所で「すべてのPC」と個々のprofileを切り替えられます。
アプリのアイコン、利用時間、サイト、曜日・時間帯、辞書で調べた語句を確認できます。
別ページでウィンドウタイトルを検索し、Google・Bingなどの検索語の履歴も閲覧できます。
外部通信、CDN、AI、Webサーバー、Node.jsは実行時に不要です。

## 初回設定と基本操作

共通設定の例です。出力先にPC名を含める必要はありません。

```yaml
schema_version: "2.3.0"
report_path: ~/.tkn/manictime_data_pipeline/reports
report_timezone: Asia/Tokyo
report_lookup_gap_minutes: 30
# 次の3つは既定値なので、通常は設定不要です。
# application_rules_path: ~/.tkn/manictime_data_pipeline/rules/application_rules.csv
# site_rules_path: ~/.tkn/manictime_data_pipeline/rules/site_rules.csv
# extraction_rules_path: ~/.tkn/manictime_data_pipeline/rules/extraction_rules.yaml
```

初回はルールファイルを作成します。各profileの `ingest` が成功した後に実行してください。

```console
tkn-manictime-pipeline rules init
tkn-manictime-pipeline build-report --all --dry-run
tkn-manictime-pipeline build-report --all
```

通常実行は書き込み後、OSの既定ブラウザで `index.html` を開きます。
`--no-open` はブラウザ起動だけを抑制します。ブラウザ起動に失敗しても生成物は残ります。
`--dry-run` は検証・集計だけを行い、ファイル、ロック、ブラウザを作成・起動しません。
進捗と出力先はstderrに表示し、このコマンドはstdoutにJSONを出力しません。

既定の出力先 `reports` は、人が閲覧するレポートをRaw・CSVの `data` と分離するためのものです。
`report_path` は自由に変更できますが、入力、Raw、CSV、stateと親子関係を含めて重なる配置は使えません。
無関係な通常ファイルは共存できます。管理対象でない `index.html` は上書きしません。

## 更新するPCと、表示するPC

通常の `build-report` は、`ingest` と同じく **default_profileだけを更新**します。
`--profile NAME` で指定PC、`--all` で設定済みの全PCを更新します。両方の同時指定はエラーです。
選択したPCに取得成功の記録がなければ、処理を止めます。

```console
tkn-manictime-pipeline build-report --no-open
tkn-manictime-pipeline build-report --profile current-pc --no-open
tkn-manictime-pipeline build-report --all --no-open
```

更新対象でない旧PCも、以前に生成したデータを使って同じ画面に表示します。
各PCの最終記録日と入力確定日時を画面に表示します。未生成のprofileも表示で知らせます。
設定から削除されたprofileでも、生成済みレポートは保持します。
現役PCが2台になったら、それぞれを `ingest` した後に `build-report --all` を使います。
分類・抽出ルールの変更後も `--all` で全PCに反映してください。異なるルールで生成されたPCが
混ざる場合は画面に表示します。異なるタイムゾーンのレポートは、`--all` で揃えるまで統合できません。

統合表示の時間は **PCごとの時間の合計**です。同時に2台を使うと重複を含むため、本人の経過時間では
ありません。記録日数と語句を調べた日数は、PCをまたいで同じ暦日を重複カウントしません。
辞書のセッションはPCごとに区別します。曜日・時間帯はPC時間を合算し、記録のある暦日数で割ります。

## 毎週の実行と再利用

このコマンドは取得処理を行いません。取得成功後に `build-report --no-open`、複数現役PCなら
`build-report --all --no-open` を実行します。タスクスケジューラにはインストール済み実行ファイルの
絶対パスと引数を設定します。必要なら `--config C:/path/to/config.yaml` を指定します。
CLI自体はスケジュールを登録しません。

入力CSV、分類・抽出ルール、集計条件、集計コードが変わったときに再集計します。
当日分は `report_timezone` の午前0時で除外し、翌日以降に対象になります。
入力の最終時刻を過ぎたPCでは、**日付が変わっただけでは再集計しません**。
HTMLテンプレートや公開処理だけの変更では、検証済みの集計結果と履歴データを再利用します。
ただし、再利用時にも入力・出力のハッシュ検証と統合ページ用のデータ読み込みは行うため、
ディスクI/OやCPU使用量が完全にゼロになるわけではありません。

週は月曜〜日曜のISO週、年・月は暦に従います。進行中や記録範囲の端にある期間も表示し、
一部だけ記録があることを示します。欠測期間を未使用のゼロとして扱いません。

## 時間の意味

| 指標 | 定義 |
| --- | --- |
| 前面表示すべて | Applications/Documentsの記録区間の経過秒数 |
| Active中の前面表示 | 上記とComputerUsageのActive判定区間が重なる秒数。ランキングの既定値 |
| サイト特定率 | URLと関連アプリの区間が重なるActive秒数 ÷ ブラウザのActive中の前面表示時間 |
| 閲覧区間数 | 対象期間に重なるURLの記録数。通信回数・ページロード回数・検索実行回数ではない |
| 曜日・時間帯 | 同じ曜日に記録のある日数で割った、1日あたりのActive分数 |

UTC時刻から指定タイムゾーンへ変換し、時間・日・週・月・年の境界で秒数を分割します。
アプリ・サイト・PCは同じ時間の異なる内訳で、相互に加算できません。
読書や視聴は入力が少ない場合もあり、これらの時間から生産性、成果、実作業時間を評価しません。

## 編集するルールファイル

`rules init` は次の場所に初期ファイルをコピーします。configへのパス指定は不要です。

```text
~/.tkn/manictime_data_pipeline/rules/
  application_rules.csv
  site_rules.csv
  extraction_rules.yaml
```

既存ファイルは編集内容ごと保持します。`rules init --dry-run` は予定だけを表示します。
別の場所を使う場合にだけ、対応する3つのパス設定を変更します。
`build-report` はキャッシュを再利用する場合も **毎回3ファイルすべてを読み込み**ます。
不足・書式エラーがあれば停止します。`rules init` は不足分だけを復元できます。
ルールなしにするには、CSVのヘッダーを残すか、抽出YAMLを `rules: []` にしてください。
インストールの更新でユーザー用ファイルは上書きしません。新しい初期例の変更は必要なものだけ取り込みます。

ルールは全profileで共有し、レポートのPC別フォルダにはコピーしません。
生成物や入力・Raw・CSV・stateの外側で管理します。CSVは通常のUTF-8（BOMも可）で、
カンマや改行を含むセルはCSV形式で引用します。取得CSV専用のバックスラッシュ表現は使いません。

### application_rules.csv：アプリの分類

生成された `application_inventory.csv` で実際の `key`・`name` と利用時間を確認してからルールを追加します。
上から最初に一致した行を採用し、コード内の既定分類より優先します。狭い条件を上に置いてください。

| 列 | 意味 |
| --- | --- |
| `device_id` | configのdevice_idと完全一致。**profile名ではありません**。空欄は全PC |
| `valid_from`, `valid_to` | 現地時刻での活動開始日。YYYY-MM-DD、両端を含む。空欄は制限なし |
| `match_field` | `key`＝Ar_Group.Key、`name`＝Ar_Group.Name、`title`＝ウィンドウタイトル、`file_name`＝メタデータの実行ファイル名（なければKeyの先頭部分） |
| `pattern` | 大文字・小文字を区別しないglob。`*`＝任意の文字列、`?`＝1文字、`[abc]`＝文字集合。正規表現ではない |
| `category` | 必須。`Browser`、`Desktop app`、`System / Shell`、`Unknown` のいずれか |
| `family` | 必須。`Chrome`、`VS Code` など、系列としてまとめる名前 |
| `channel` | 任意。`Dev`、`Beta`、`Stable` など。一致したルールの空欄は `Unknown` |
| `purpose` | 任意の用途ラベル。CSV/JSONに保存。現時点ではHTMLの用途フィルターはない |

```csv
device_id,valid_from,valid_to,match_field,pattern,category,family,channel,purpose
,,,title,*Google Chrome Dev,Browser,Chrome,Dev,Research
,,,file_name,code.exe,Desktop app,VS Code,,Development
```

1行目は末尾がGoogle Chrome Devのタイトル、2行目はVS Codeの実行ファイルを分類します。
既定では一般的な実行ファイルを分類し、それ以外は未分類に残します。
現在のグループのインストール先から過去のChromeチャネルを推定しません。
特定PC・期間を限定するルールも、その期間に分類が正しかったという別の根拠がある場合に使ってください。
ManicTimeのPNGアイコンは重複を除いてHTMLに埋め込み、アプリ名の左に表示します。
画像がないアプリは空欄になります。画像からチャネルを判定しません。

### site_rules.csv：サイトの表示名と用途

| 列 | 意味 |
| --- | --- |
| `host` | 必須。`ejje.weblio.jp` などのホスト名。小文字に変換して完全一致。URL・パス・ワイルドカードは使わない。末尾のドットを無視し、`www.` は区別する |
| `service` | 必須。`Weblio` などの表示名 |
| `purpose` | 任意。`Dictionary` など、サイト詳細表に表示する用途 |

```csv
host,service,purpose
ejje.weblio.jp,Weblio,Dictionary
```

ホストの重複はエラーです。サブドメインやサービス単位にランキングを自動統合しません。
未登録のホストもホスト名のままランキングに出ます。
このCSVは表示名の補足で、**辞書の語句抽出を有効にする設定ではありません**。
辞書や検索語の抽出は、次のYAMLが担当します。

### extraction_rules.yaml：辞書・検索語の抽出

`build-report` で必ず読むルールファイルです。一般設定の `config.yaml` と分離しています。
`schema_version: "1.0.0"` と `rules` のリストを持ちます。

| フィールド | 意味 |
| --- | --- |
| `id` | 必須。一意で空でないルール名 |
| `kind` | 必須。`dictionary` または `search` |
| `hosts` | 必須。小文字のホスト名の配列。ここでは先頭の `www.` を取り除いて完全一致する。Googleの他の地域ホストは明示的に追加する |
| `path_pattern` | 必須。URLのパスをパーセントデコードして照合するPython正規表現。大文字・小文字を区別しない。辞書では最初のキャプチャ括弧が見出し語。検索では検索結果ページのパスを限定する |
| `title_pattern` | 辞書用の任意設定。URLから抽出できない場合、関連アプリのタイトルに照合する。最初のキャプチャ括弧が見出し語 |
| `query_parameter` | 検索語が入るURLパラメーター名。searchでは必須。Google/Bingは `q`。辞書では既定が `q` で、検索時の表記として保存する |
| `strip_suffix` | 辞書で抽出した語句から取り除く正規表現。例：`'_\d+$'` はOxfordの語義番号を除去する |

```yaml
schema_version: "1.0.0"
rules:
  - id: weblio
    kind: dictionary
    hosts: [ejje.weblio.jp]
    path_pattern: '^/content/([^/]+)'
    title_pattern: '^(.+?)の意味(?:・使い方)?'
  - id: google
    kind: search
    hosts: [google.com, google.co.jp]
    path_pattern: '^/search/?$'
    query_parameter: q
```

正規表現をYAMLで書くときは、バックスラッシュを保持する単一引用符が便利です。
同じkindの中で最初に抽出できたルールを採用します。searchはパスの一致と空でない検索語が必要で、
タイトルだけから検索実行を推測しません。初期ファイルにはWeblio・Cambridge・Oxford・
Google（.com/.co.jp）・Bingを含めています。
変更後は `build-report --all --dry-run` で実データを使って検証し、`--all --no-open` で反映します。

## 辞書で調べた語句

URLの見出し語を優先し、設定したタイトル形式を代替に使います。
見出し語、検索表記、URL、関連ウィンドウタイトル、CSV、タイムラインID、活動IDを保持します。
NFKC正規化、小文字化、設定した語義番号の除去を行いますが、活用形や意味の自動統合はしません。

同じPC・同じ日・同じ語句を30分以内の間隔で続けて閲覧した区間は、辞書をまたいでも1セッションにします。
間隔は `report_lookup_gap_minutes`（1〜240分）で変更できます。日数、セッション数、閲覧区間数は別の値で、
閲覧開始日に帰属します。抽出できなかった辞書記録は別CSVに残します。
HTMLの根拠表は500件までで、CSV/JSONに全件を保存します。

## タイトル検索と検索語の履歴

メイン画面の履歴リンクから開くと、PCと期間を引き継ぎます。直接開いた場合の初期値は最新記録から30日間です。
「全期間」で日付制限を解除できます。履歴の種類を選び、含まれる文字を入力して「検索」を押します。
部分一致で、大文字・小文字や全角・半角を区別しません。

現地時刻の表示開始・終了、PC、アプリ、タイトルまたは検索語、Active秒数を表示します。
各行の「記録の根拠」で元CSV・ReportId・ActivityIdを確認できます。検索語には元URLもあります。
Active秒数は、その記録区間とPCのActive判定が重なる時間です。

履歴は月別のJavaScriptデータに保存し、重複する文字列は辞書化して容量を抑えています。
検索時に該当する月だけを順次読み込みます。全一致件数と最初・最後の日時を示し、
**新しい順に1,000件まで、1ページ50件**で表示します。さらに古い結果は日付を絞って確認してください。
中止は現在のファイル読み込み後に反映します。読み込み失敗や中止時は途中結果であることを明示します。
日付の条件は記録の開始日です。

これらはManicTimeの前面表示・URL区間の記録で、ブラウザの履歴を別途読み取るものではありません。
検索結果への再訪も含み、検索実行回数や動画の視聴完了、初めて知った正確な瞬間を証明しません。
URLが記録されていない検索語は復元できません。タイトル全件はメインHTMLや `data.json` に入れません。

## 保存構成とfingerprint

```text
<report_path>/
  index.html
  report-manifest.json
  history-<hash>.html
  devices/<device_id>/<fingerprint>/
    index.html
    data.json
    daily.csv
    applications.csv
    domains.csv
    dictionary_events.csv
    unresolved_dictionary.csv
    application_inventory.csv
    manifest.json
    history/YYYY-MM.js
```

`<fingerprint>` は **入力CSV、分類・抽出ルール、集計条件、生成コード、HTMLテンプレートから計算した
SHA-256の値**です。PCのIDや毎回変わる乱数ではなく、生成条件を識別します。
表示だけの変更でも別の値になるため、見た目が似た世代が並ぶことがあります。
集計用の署名を別に持ち、表示だけの変更では集計結果を再利用します。

入口HTMLには生成済みPCの集計データ、履歴HTMLには小さなファイル一覧を埋め込みます。
PC別フォルダは再利用用の集計結果とCSVの保存先として残ります。
補助CSVは秒数・UTF-8 BOM付き・リストセルはセミコロン区切りです。
表計算ソフトが数式として実行しうる文字列には先頭にアポストロフィを付け、原文はJSONに残します。
アプリ・サイトCSVを再集計する際は `period` を1種類に絞ってください。複数の時間軸を加算すると重複します。

入力は取得成功manifestとCSVの全ハッシュで検証します。device_idと内容が同じであれば、移動したCSVを
元のprofile名・パスが異なっていても読み取れます。ingest自身の厳密な同一性チェックは変更しません。
通常実行はingestと同じロックを共有し、未復旧の更新があれば停止します。
同一タイムラインの重複区間はエラーにし、不正時刻・非正区間・当日除外などは品質情報に記録します。

選択PCの生成がすべて終わってから入口を切り替えます。履歴ページも最新と直前を保持します。
PCごとに最新と直前の世代を残し、それより古い世代は
manifestに載るファイルの内容と配置を検証してから削除します。編集・追加ファイルがある世代は保持します。
中断後は同じコマンドを再実行できます。同一世代の編集済みファイルを上書きしません。
入力、Raw、ingestの履歴は変更しません。生成HTMLを直接編集せず、ルールから調整してください。

## docsのJSON例について

[`current.example.json`](current.example.json) はingestの確定済み実行を指すポインター、
[`manifest.example.json`](manifest.example.json) はingestの実行記録の保存形式を示す、読み手向けの見本です。
ハッシュは仮の値で、実行可能な設定ではありません。READMEから参照し、アプリのコードは読み込みません。
レポートのmanifestとは別です。保存形式の説明なので `docs` に置いています。
一方、インストールしたコマンドが読むHTML・設定・ルールの初期ファイルは
`src/manictime_pipeline/resources` に含めます。

Itadakiの入力イベントとの突き合わせは次の段階です。入力先アプリや生産性は推測しません。
