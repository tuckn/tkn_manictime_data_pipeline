# PC利用レポート

`build-report` は取得済みCSVからローカルHTMLを生成します。入口は一つの `index.html` です。
年・月・週のフィルターと同じ場所で「すべてのPC」と個々のprofileを切り替えられます。
アプリのアイコン、利用時間、サイト、曜日・時間帯、辞書で調べた語句を確認できます。
別ページでウィンドウタイトルを検索し、Google・Bingなどの検索語の履歴も閲覧できます。
生成済みHTMLの閲覧に外部通信、CDN、AI、Webサーバー、Node.jsは不要です。
分類を編集する任意の `review` コマンドだけは、PC内のWebサーバーを使います。

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
# application_overrides_path: ~/.tkn/manictime_data_pipeline/rules/application_overrides.yaml
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
進捗・警告はstderrへ、最終結果（`index`、`report_path`、`dry_run`、`devices`、件数）は
stdoutへ `key=value` で表示します。`--json` ならJSONになります。
[CLI出力の仕様](reference/cli-output.md)を参照してください。表示指定で生成物やブラウザの動作は変わりません。

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
  application_overrides.yaml
  site_rules.csv
  extraction_rules.yaml
```

既存ファイルは編集内容ごと保持します。`rules init --dry-run` は予定だけを表示します。
別の場所を使う場合にだけ、対応する4つのパス設定を変更します。
`build-report` はキャッシュを再利用する場合も **毎回4ファイルすべてを読み込み**ます。
不足・書式エラーがあれば停止します。`rules init` は不足分だけを復元できます。
ルールなしにするには、CSVのヘッダーを残すか、抽出YAMLを `rules: []` にしてください。
インストールの更新でユーザー用ファイルは上書きしません。新しい初期例の変更は必要なものだけ取り込みます。

ルールは全profileで共有し、レポートのPC別フォルダにはコピーしません。
生成物や入力・Raw・CSV・stateの外側で管理します。CSVは通常のUTF-8（BOMも可）で、
カンマや改行を含むセルはCSV形式で引用します。取得CSV専用のバックスラッシュ表現は使いません。

### ブラウザ編集と application_overrides.yaml

更新後は `rules init` を再実行してください。既存の設定を保持し、新しいYAMLだけを追加します。

```console
tkn-manictime-pipeline rules init
tkn-manictime-pipeline review
```

`review` は全profileのレポートを更新し、`http://127.0.0.1:8767/editor` を開きます。
最終結果はサーバー停止後に `action=stopped` としてstdoutへ表示し、`--json` でJSONにできます。
サーバー稼働中のCtrl+Cは従来どおり正常停止（終了コード0）、準備・再生成中の中断は130です。
全profileで事前に `ingest` が成功している必要があります。記録済みアプリを検索し、種類・用途の
ルールを追加・変更して、「変更内容を確認する」→「この変更を保存してレポートを更新」と進みます。
保存前にはYAMLの差分が表示されます。狭い条件のルールを上へ移動してください。
保存すると全PCを再集計します。開いたままのレポートは再読み込みで更新されます。

`--no-open` はブラウザを開かずURLを表示します。ポートが使用中なら `--port 0` で空きを選び、
表示されたURLを開いてください。終了は Ctrl+C です。サーバーはこのPC内のループバックだけで動き、
外部サービスにデータを送りません。生成済みHTMLは引き続きサーバーなしで閲覧できます。

保存先の既定値は `~/.tkn/manictime_data_pipeline/rules/application_overrides.yaml` です。
移動する場合だけ `application_overrides_path` を指定します。毎週の `build-report` でも必ず読みます。
配布時の初期ファイルには個人的な用途判断を含めず、空のルールを用意しています。

```yaml
schema_version: "1.0.0"
classifications:
  - id: edge-beta-title
    match:
      file_name: msedge.exe
      title: '*Microsoft Edge Beta'
    set:
      category: Browser
      family: Edge
      channel: Beta
engagement:
  - id: edge-beta-focus
    match:
      family: Edge
      channel: Beta
    set:
      engagement: focus
  - id: personal-apps
    match:
      group_path_prefix: 'C:\path\to\personal-apps'
    set:
      engagement: distracted
```

2つのリストを順に処理し、それぞれで最初に一致したルールだけを採用します。

| リスト | 判定・変更内容 |
| --- | --- |
| `classifications` | 内蔵判定・分類CSVの結果を元に、指定した `category`・`family`・`channel`・`purpose` だけを変更。YAMLがCSVより優先されます。 |
| `engagement` | 変更後の種類を含む条件で用途を判定。`focus`＝集中、`distracted`＝だらけ、`other`＝その他、`unknown`＝用途未分類。一致しなければ用途未分類です。 |

各ルールは、一意で空でない `id`、空でない `match`、空でない `set` が必須です。
条件に使えるのは `file_name`・`key`・`name`・`title`・`family`・`channel`・`device_id`・
`group_path_prefix` です。複数指定はすべてに一致した場合に適用します。
フォルダ条件以外は、大文字小文字を区別しないワイルドカード（`*`・`?`・`[abc]`）で、正規表現ではありません。
フォルダ条件はWindowsのパスをそのまま指定し、フォルダ境界を含めて照合します。区切り文字はどちらでも使えます。
似た名前の隣接フォルダやパス不明の記録には一致しません。
`file_name` はメタデータに値がない場合、ManicTimeのキーの先頭部分を使います。
任意の `valid_from`・`valid_to` は引用した `YYYY-MM-DD`、両端を含み、レポートのタイムゾーンでの
活動開始日を基準にします。日付をまたぐ活動にも開始日の判定を適用します。
大分類は `Browser`・`Desktop app`・`System / Shell`・`Unknown`、系列・種類・用途メモは空でない文字列です。
リストを `[]` にすると、その段階の上書きは無効になります。

ManicTimeのグループの実行パスは後のインストール状況を表す場合があり、**過去の種類を証明しません**。
フォルダ条件は、この共有メタデータを利用者の明示指定で適用するものです。
ブラウザ種類は、タイトルや確認済みのPC・期間条件などで設定してください。
単に `Google Chrome` としか残っていない記録は種類不明です。通常版・家族との交流・集中へ自動で振り分けません。
Betaの明示や確かなカスタム条件がある場合だけ区別できます。
アプリの種類と用途は独立しており、レポート上部で大分類 → 系列 → 詳細 → 用途を切り替えられます。

保存時にYAMLの書式を整えるため、コメントは除去されます。変更前の原本 `before.yaml`、変更後の
`after.yaml`、処理結果 `decision.json` を `<state_path>/rule-edits/<日時-ID>/` に保持します。
4つのルールファイルの版を確認し、古い画面からの上書きを防ぎます。レポート生成と保存は同じロックで調整します。
設定保存後に集計が失敗した場合は、その状態を画面に表示します。同じ変更を再保存せず、原因を解決して
`build-report --all` を実行してください。前のレポートは残ります。DBや取得済みCSVは変更しません。

### 集中・だらけの時間帯

従来のActiveヒートマップに加え、各マスに集中を左の青丸、だらけを右の橙丸で表示します。
丸の**面積**が時間に比例し、両者は共通の尺度です。差し引きはしません。
小さい輪は0分、`—` は記録日なしです。マスの選択・キーボードフォーカスで正確な分数を確認でき、
数値表ではその他・用途未分類も確認できます。4つの合計はActive中のアプリ時間を表します。

活動区間と ComputerUsage/Active の重なりを求め、ローカル時刻の時・日の境界で分割します。
同じ曜日の記録のある暦日数で割った「分/日」で、4用途とも同じ分母です。一部だけ記録された日も含み、
未観測時間を補完しません。複数PCは同時利用も足し、その後に重複しない日数で割るため、60分を超えることがあります。
ランキングの時間設定にかかわらず、この図はActive中のアプリ時間を使います。
利用者の用途ラベルに基づく内訳であり、実際の集中・フロー・入力・目標達成を判定するものではありません。
日別CSVに `<role>_seconds`・`<role>_hourly`、アプリCSVに `engagement` を収録します。
分類一覧には適用したルールID、実行ファイル・グループのメタデータ、最大3件のタイトル例も含みます。
全タイトルは従来の履歴ページから確認してください。

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
