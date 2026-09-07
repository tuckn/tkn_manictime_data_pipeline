# PC利用レポート

`build-report`は、取得済みCSVからPC別のローカルHTMLを作成します。
全期間・年間・月間・週間をページ内で切り替え、アプリ、サイト、曜日と時間帯、辞書で調べた語句を確認できます。
外部通信、CDN、生成AI、別のWebサーバー、Node.jsは不要です。

## 設定と使い方

通常のユーザー設定は`~/.tkn/manictime_data_pipeline/config.yaml`です。
既存の取得設定に次の共通設定を加えます。プロファイル内には置きません。

```yaml
schema_version: "2.2.0"
report_path: ~/.tkn/manictime_data_pipeline/reports
report_timezone: Asia/Tokyo
report_lookup_gap_minutes: 30
# 必要なときだけ指定する、ユーザー管理の補足CSV:
# application_rules_path: C:/path/to/application_rules.csv
# site_rules_path: C:/path/to/site_rules.csv
```

`report_path`の既定値は、Raw/CSVを置く`data`と区別した`reports`です。
指定したフォルダ直下に`index.html`を作り、その下の`devices`にPC別レポートを置きます。
CLIがデバイス別フォルダを作るため、設定値にPC名を含めません。
入力・Raw・抽出CSV・stateと互いに包含する出力先は拒否します。
通常のファイルが置かれたフォルダを使うことはできますが、未管理の`index.html`を上書きしません。

```console
tkn-manictime-pipeline build-report --dry-run
tkn-manictime-pipeline build-report
```

通常実行はHTMLと補助CSVを生成し、完成した入口をOS既定のブラウザで開きます。
`--no-open`はブラウザ起動だけを抑止します。ブラウザ起動失敗でも生成結果は保持し、パスを表示します。
進捗と結果のパスは標準エラーに表示し、このコマンドは標準出力へJSONを追加しません。

既定では、設定にある**全プロファイル**が対象です。`default_profile`は対象を絞りません。
これは1プロファイルを取得する`ingest`との違いです。
`--profile NAME`を付けると1台だけ更新し、既存の他PCへの入口を維持します。
全対象に成功済みの取得記録が必要で、不足していれば明示的に停止します。

```console
tkn-manictime-pipeline build-report --profile current-pc --no-open
```

## 毎週の運用

先に対象PCの`ingest`を成功させ、その後で次を実行します。

```console
tkn-manictime-pipeline build-report --no-open
```

レポートコマンド自体は取得処理を呼びません。Windows Task Schedulerには、インストール済み
`tkn-manictime-pipeline`の絶対パスと上の引数を指定できます。設定は絶対パスを使い、必要なら
`--config C:/path/to/config.yaml`を指定してください。CLIはスケジュールを登録しません。

手動で期間を指定する必要はなく、保存済みの全履歴を対象にします。入力CSV、分類ルール、
集計日境界、生成ロジックが変われば再生成し、同じ条件で出力が揃っていれば再利用します。
当日分はレポートのタイムゾーンにおける午前0時で切り、翌日の実行以降に反映します。
週は月曜〜日曜、月・年は暦です。進行中または記録範囲の端にある期間も表示し、一部期間であることを示します。

## 時間と分類の意味

- **前面表示時間**: Applications/Documentsに記録された区間の経過秒数。
- **Active中の前面表示時間**: 上記とComputerUsageのActive区間との重なり。ランキングの既定です。
- **サイト特定率**: Active中のブラウザ時間のうち、関連アプリと重なり、URLを特定できた時間の割合。
- **閲覧区間数**: 各期間に重なる元のURL記録の数。ページ読み込み、通信、検索の回数ではありません。
- **曜日・時刻**: 同じ曜日の記録のある日数で割った、1日あたりのActive分数。

時間はUTCの実時間から計算し、指定タイムゾーンの時間・日境界で分割します。
日付をまたぐ区間を開始日に一括加算せず、ISO週年と暦年も区別します。
アプリ、サイト、PCのActive時間は同じ時間を別の観点で見た値であり、加算できません。
記録なしは利用ゼロとせず、チャートに欠測を残します。入力のない読書・視聴もあるため、成果や実作業時間の評価はしません。

アプリは`Browser`、`Desktop app`、`System / Shell`、`Unknown`で分類します。
代表的な実行ファイルに既定分類があり、該当しないものは未分類として保持します。
Chrome/Edgeの現在のグループ実行パスから過去のチャネルを推定しません。
タイトル末尾の明示的なBeta/Devなど、またはユーザーの補足ルールで識別できたものだけ区別します。
系列別・大分類別では日付の集合を統合し、複数チャネルを使った日を重複して数えません。

## 補足CSV

同梱例は`src/manictime_pipeline/resources/application_rules.example.csv`と
`site_rules.example.csv`です。ユーザー管理の場所にコピーし、必要なら設定へパスを追加します。
補足CSVはレポート出力先および抽出CSVフォルダとは別に置きます。
生成された`application_inventory.csv`で、長く使う未分類アプリから確認できます。

アプリ分類CSVの列は次のとおりです。

```text
device_id,valid_from,valid_to,match_field,pattern,category,family,channel,purpose
```

上から最初に一致した行を使い、組み込み分類より優先します。`match_field`は`key`、`name`、
`title`、`file_name`のいずれかです。`pattern`は大文字小文字を区別しないglobで、`*`などを使えます。
PCと適用日（YYYY-MM-DD、両端を含む）は空欄なら制限なしです。適用日は活動開始日の現地日付です。
`category`と`family`は必須で、`channel`と`purpose`は任意です。
実行パスが失われた履歴のチャネルを補足CSVだけで復元できるわけではありません。

サイト分類CSVは`host,service,purpose`です。小文字に正規化したホスト名で完全一致し、重複は拒否します。
サブドメインを勝手に統合しません。サービス名と用途はサイト明細へ表示します。

## 辞書の語句

Weblio英和、Cambridge、Oxford Learner's DictionariesのURLから見出し語を抽出し、
対応するURL形式でなければ既知のタイトル形式を補助に使います。検索表記、見出し語、URL、
タイトル、元CSV・タイムライン・活動IDを保持します。タイトルはURL記録の関連アプリIDから取得します。
単純な小文字化・Unicode正規化とOxfordの語義番号除去を行い、活用形の自動統合や意味の推測はしません。

同じ語句を同じ日に既定30分以内の間隔で続けて見た記録を、辞書をまたいでも1セッションにまとめます。
`report_lookup_gap_minutes`は1〜240で変更できます。日数・セッション数・区間数を区別し、
閲覧開始日で数えます。抽出不能の辞書記録も別CSVへ残します。
画面の閲覧明細は先頭500件までで、全件はCSV/JSONへ保存します。

## 出力と保存の契約

```text
<report_path>/
  index.html
  report-manifest.json
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
```

1PCのHTMLには全期間の集計と画面処理を埋め込み、ファイルを開くだけで切り替えられます。
補助CSVの時間単位は秒で、UTF-8 BOM付きです。リスト列は`;`区切りです。
タイトル等が表計算の式として解釈される場合はCSVで先頭に`'`を付けます。元の文字列はJSONに保持します。
アプリやサイトのCSVは複数の時間軸を収録するため、`period`を絞って使用してください。
異なる時間軸を合計すると同じ活動を重複計上します。

入力は成功済み取得記録のmanifestと、全CSVのハッシュを照合します。PCの識別子と内容が同じなら、
保存先やプロファイル名が取得時から変わったCSVもレポートでは読めます。取得側の厳密な同一性検査は変更しません。
通常実行は取得側と共通のロックで同時更新を防ぎ、復旧待ちの取得があれば停止します。
dry-runはロックファイルも作らず、前後の取得記録とCSVを再照合します。
同じタイムライン内の重複区間は曖昧な二重計上を避けるためエラーにします。
不正時刻、長さが正でない記録、当日分の除外件数は品質情報に記録します。

PC別の世代がすべて完成してから入口を切り替えます。最新と直前の生成世代を保持し、
さらに古い世代はmanifestにあるファイルだけをハッシュ検証後に削除します。
手編集・追加ファイルがある古い世代は保持します。入力・Raw・stateの取得記録を削除・変更しません。
中断後は同じコマンドを再実行できます。完成していない世代は入口からリンクしません。
同じ世代の手編集があれば上書きせず停止します。レポートは生成物なので、変更は分類CSVへ反映してください。

Itadakiの入力イベントとの統合は次段階です。今回のレポートは入力先や生産性を推定しません。
