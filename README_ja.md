# ManicTime Data Pipeline

[English](README.md)

ManicTime の SQLite DB の最新コピーを Raw として保存し、再利用できる CSV を抽出する CLI です。
初回は全期間の活動と関連テーブルを出力し、次回からは変更のあった月・テーブルだけを更新します。
Raw は最新1世代を保持し、CSV も固定パスに最新データを保存します。来歴・チェックポイントは state に集約します。
この CLI の範囲は取得・抽出・実行記録・検証です。HTML レポート、分類ルール、生成 AI の助言、
他ソースとの統合は今後の対象です。

## 使い方 — 最初の結果まで

通常実行は保存・更新を行います。`--dry-run` を付けると、入力と前回の出力を比較し、
パイプラインのデータ・設定・state・cache・一時ファイルを書き込まずに予定を確認します。

| コマンド   | 得られるもの                                                |
| ---------- | ----------------------------------------------------------- |
| `ingest` | 両 DB の Raw 保存、変更判定、CSV 抽出、最新 manifest の公開 |
| `verify` | CSV・Raw のハッシュと、Raw に対する CSV の一致の検証        |

### 必要環境とインストール

**動作確認環境：ManicTime 2023.1.1.0（64-bit）、Standard（無料版）。**

Python 3.11 以上と標準の `sqlite3` モジュール、uv を使用します。Windows を主な検証対象としています。
システムの `sqlite3.exe`、起動中の ManicTime UI は不要です。
PyYAML はパッケージとともに導入されます。CLI の処理に通信や生成 AI 呼び出しはありません。

次のリポジトリ例を実際のパスに置き換えて実行します。

```console
cd "C:\path\to\tkn_manictime_data_pipeline"
uv tool install .
tkn-manictime-pipeline --help
tkn-manictime-pipeline config init
```

表示された `~/.tkn/manictime_data_pipeline/config.yaml` を編集します。

- `profiles.current-pc.source_path`: `Data` を含む ManicTime 本体のフォルダ、または DB 格納フォルダ。
- `profiles.current-pc.device_id`: データ保存先のサブフォルダ名に使用する、他のプロファイルと重複しない名前。
- `raw_path`: ManicTime の DB コピーを保存する親フォルダ。既定値は `~/.tkn/manictime_data_pipeline/data/raw`。
- `processed_data_path`: 抽出した CSV データを保存する親フォルダ。既定値は `~/.tkn/manictime_data_pipeline/data/csv`。

既定の保存先を使う場合、最初に変更するのは `source_path` と `device_id` だけです。
両方の保存先は共通設定で、必要なプロファイルだけ個別に上書きできます。
どちらも CLI が親フォルダの下へ `device_id` を一度だけ追加するため、保存先の設定には含めません。

入力には `ManicTimeCore.db` と `ManicTimeReports.db` の両方が必要です。
Raw・CSV・実行記録の保存先は、`source_path` に指定したフォルダの外にしてください。
ManicTime 本体のフォルダを指定した場合は本体フォルダ、DB 格納フォルダを直接指定した場合は
そのフォルダの外を意味します。入力と出力のフォルダを互いに包含する配置もできません。
[同梱の設定例](src/manictime_pipeline/resources/config.example.yaml)に初期設定全体があります。

```console
tkn-manictime-pipeline config show
tkn-manictime-pipeline ingest --dry-run
tkn-manictime-pipeline ingest
tkn-manictime-pipeline verify
```

日常の更新も同じ `ingest` を日次・週次で実行します。初回は全履歴、以後は直前に
公開できたデータとの比較になります。当日のコミット済みデータも含み、記録中の活動時間の延長は
次回に反映します。

### 結果を確認・利用する

実行結果の絶対パスは標準出力の JSON に、進捗は標準エラーに表示されます。

| 保存先 | 役割 |
| --- | --- |
| `<raw_path>/<device_id>/ManicTimeCore.db` | 最後に成功した Core のコピー |
| `<raw_path>/<device_id>/ManicTimeReports.db` | 最後に成功した Reports のコピー |
| `<processed_data_path>/<device_id>/Ar_Activity/YYYY/MM.csv` | 各月の最新活動データ |
| `<processed_data_path>/<device_id>/<table>/all.csv` | 関連・メタデータテーブルの最新データ |
| `<state_path>/<profile>/current.json` | 最後に成功した実行記録を指すチェックポイント |
| `<state_path>/<profile>/runs/<run-id>.json` | 取得来歴、schema、CSV のハッシュ・件数、実効設定、実行結果 |
| `<state_path>/<profile>/transactions/<run-id>/` | 更新準備中の CSV、復旧用の旧 CSV、復旧手順の記録 |

`state_path` の既定値は `~/.tkn/manictime_data_pipeline/state` です。プロセスのロックもここに置きます。
**state は削除可能なキャッシュではありません。Raw・CSV とともにバックアップしてください。**
削除するとチェックポイントを失い、既存 CSV がある状態での ingest は停止します。
Raw フォルダには両 DB を保存します。更新中だけ、作業用 `.partial` フォルダに候補・旧 DB を一時配置します。
取得時刻・入力パス・サイズ・ハッシュは state の実行記録に保存します。
形式のバージョンは JSON 内に記録し、CSV のパスにはバージョンのフォルダを加えません。

ingest 成功後は、Power BI や Python などから活動 CSV を直接読み込めます。

```python
from pathlib import Path

root = Path("C:/path/to/csv/Example Current PC")
activity_csvs = sorted((root / "Ar_Activity").glob("*/*.csv"))
groups_csv = root / "Ar_Group" / "all.csv"
```

通常のデータ利用で manifest をたどる必要はありません。来歴を調べる場合は state の
`current.json` と、そこに記載された実行記録を読みます。artifact のパスはデバイス別 CSV フォルダ基準です。
過去の実行記録は当時のハッシュを表しますが、固定 Raw・CSV パスの内容は最新データです。
過去の実行記録だけでは、当時の DB や CSV を再現できません。最新データは最新 Raw から再抽出できます。
過去の状態への復元が必要な場合は、別途バックアップを保持してください。

`all.csv` は、関連・メタデータの1テーブルの全行をまとめた最新の CSV です。
例えば `Ar_Group/all.csv`、`Ar_Timeline/all.csv` があり、年月で分割するのは `Ar_Activity` だけです。

Raw・CSV は **ingest の成功後**に読み込んでください。置換はファイル単位で行うため、
更新中に複数ファイルを読むと、新旧のデータが混在する場合があります。

## コマンド一覧

| 目的                                       | コマンド                    |
| ------------------------------------------ | --------------------------- |
| 編集済み内容を上書きせずユーザー設定を作成 | `config init [--dry-run]` |
| 解決後の値と、その値を決めた設定元を確認   | `config show`             |
| 入力 schema・件数・活動期間を確認          | `inspect`                 |
| Raw 保存と CSV 差分更新                    | `ingest [--dry-run]`      |
| 最新 CSV と対応する Raw を検証             | `verify`                  |
| 中断した CSV 更新を復旧 | `recover [--dry-run]` |

共通オプションは `--config PATH`、`--profile NAME`、
`-q/--quiet`、`-v/--verbose` です。
コマンドの前後に指定できます。quiet と verbose は同時指定できません。
`config init` は常にユーザー設定を対象とし、`--config`・`--profile` を受け付けません。
同じテンプレートがあれば unchanged、編集済みなら保持したうえでエラーになります。

終了コードは成功 0、設定・処理エラー 1、引数の使用誤り 2、中断 130 です。
quiet でも標準出力の結果 JSON は表示されます。verbose ではエラーの traceback も表示します。
対応した対話端末でのみ ANSI 色を使い、`NO_COLOR` に従います。

`ingest` の created / updated / unchanged は **CSV データセット**に対する状態です。
CSV が unchanged でも両 DB の取得と実行記録の保存は行います。Raw の内容が変わっていれば
最新ファイルを置換しますが、実行ごとのアーカイブは増やしません。
removed は最新一覧と固定パスの両方から削除するパーティション数です。
置換・削除するのは管理対象データのみです。入力 DB は保持し、置換した旧 Raw は成功確定後に削除します。

## 設定の詳細

各 YAML は `schema_version: "2.1.0"` を持ちます。2.0.x と 2.1.x に対応し、
未対応の major/minor、キーの重複・未知キー、型の不一致はエラーにします。
各設定元をマージ前に検証するため、上位設定で下位設定の誤りを隠すことはできません。
state・実行記録はスキーマ 3.0.0 と最新 Raw 配置を使用します。
それ以外の保存形式はエラーとし、自動変換しません。

優先順位は built-in → ユーザー設定 → 実行時の `.tkn/config.yaml`
→ `--config` → CLI でのプロファイル選択です。
profiles は名前、次にプロパティ単位でマージします。
`config show` には各入力の schema version、内部 schema version、解決後のパス、
値ごとの設定元を表示します。読み込みで設定を書き換えません。

`--config` を省略しても、`~/.tkn/manictime_data_pipeline/config.yaml` と実行時の
`.tkn/config.yaml` を自動で確認し、存在するファイルを読み込みます。
最初に見つかった1ファイルだけを使うのではなく、設定を統合します。
明示した `--config` のファイルが存在しない場合はエラーです。
`--profile` は統合後の設定にあるプロファイル名を選ぶ引数で、ファイルやフォルダの指定ではありません。
省略時は `default_profile` が `profiles` のキー名と一致する必要があります。
例えば `profiles.current-pc` を `profiles.desktop` に変更したら、`default_profile: desktop`
も合わせて変更します。不明な名前から別のプロファイルを推測して実行することはありません。
選択エラーでは指定名・その設定元・選択可能な名前・読み込んだ設定ファイルを表示します。
統合時にプロファイル名が異なるものは別々に残り、`device_id` が同じなら重複エラーになります。
重複エラーには対象のプロファイル名と設定ファイルを表示します。


| キー | 意味 |
| --- | --- |
| `default_profile` | `--profile` 省略時の対象 |
| `raw_path` | DB コピーの保存先の親フォルダ。既定値は `~/.tkn/manictime_data_pipeline/data/raw` |
| `processed_data_path` | CSV データ出力先の親フォルダ。既定値は `~/.tkn/manictime_data_pipeline/data/csv` |
| `state_path` | チェックポイント・来歴・実行記録・復旧用データの保存先。既定値は `~/.tkn/manictime_data_pipeline/state` |
| `backup_timeout_seconds` | DB ごとの backup の制限秒数。既定 300、整数 1～86400 |
| `max_activity_drop_percent` | 許容する活動行数の減少率。既定 10、数値 0～100。後述の停止条件を参照 |
| `profiles.<name>.device_id` | 必須。データ保存先のサブフォルダ名。他のプロファイルと重複せず、Windows で使用できる名前 |
| `profiles.<name>.source_path` | 必須。ManicTime 本体のフォルダまたは DB 格納フォルダ |
| `profiles.<name>.raw_path` | 任意。共通の DB 保存先の親フォルダを上書き |
| `profiles.<name>.processed_data_path` | 任意。共通の CSV 保存先の親フォルダを上書き |

保存先ごとに、プロファイルの指定があればその値、なければ共通設定を使います。
下位の設定ファイルでプロファイル別に指定した値は、上位ファイルの共通設定より優先します。
変更する場合は上位ファイルでもそのプロファイルの値を指定します。
共通設定も省略した場合は、組み込みの既定値を使います。
`config show` の `effective_profiles` には、継承・上書き後の親フォルダ、実際のデバイス別保存先、
それぞれの親フォルダを決めた設定元を表示します。

例えば、過去 PC だけ別の保存先を指定し、他のプロファイルには既定値を使用できます。

```yaml
schema_version: "2.0.0"
default_profile: historical-pc
profiles:
  historical-pc:
    device_id: Example Historical PC
    source_path: C:/path/to/historical/ManicTime
    raw_path: C:/path/to/archive
    processed_data_path: C:/path/to/csv
```

この例の保存先は `C:/path/to/archive/Example Historical PC/*.db` と
`C:/path/to/csv/Example Historical PC/` です。追加の `Raw` フォルダは作りません。
`device_id` を変更すると保存先も変わるため、既存データの表示名変更には使用しないでください。

`~` は実行ユーザーのホームに展開します。
相対パスは、どの設定元でも YAML の場所ではなく実行時のカレントディレクトリを基準にします。
インストール済み CLI がリポジトリ内の設定を読むのは、そこで実行した場合か、
`--config` で指定した場合です。実設定、DB、実行結果は Git 管理外に置きます。

過去 PCの DB には、別名のプロファイルと異なる `device_id` を追加できます。
元のアーカイブを入力にする場合も、Raw 出力は別フォルダに指定してください。
1回の実行では選択した1プロファイルだけを処理し、他の入力を自動取得しません。
公開済みデータセットは、解決後の入力パス・Raw ルート・device ID と結び付きます。
CSV 保存先とプロファイル名も state と結び付きます。これらの変更は拒否します。
既存データの移動やプロファイル名の変更には、別途、検証を伴う手順が必要です。

### Windows Task Scheduler

日次・週次のタスクでインストール済み実行ファイルを呼び出します。
`Get-Command tkn-manictime-pipeline` で取得した絶対パスをプログラムに指定し、
次を引数にします。

```console
--config "C:\path\to\config.yaml" --profile current-pc ingest
```

定期実行用設定のデータパスには絶対パスを推奨します。
実行アカウントのホームが設定・state の基準になります。
Task Scheduler は重複起動しない設定にします。CLI は解決後の Raw・CSV 保存先ごとのロックを state に置きます。
同じデータ保存先へ書き込む実行では、同じ `state_path` と Windows アカウントを使用してください。
ブラウザは起動しません。このリポジトリはスケジュールを自動登録・置換・無効化しません。

## 保存・抽出・復旧の仕様

### Raw 保存

Python の [SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)
を使用します。[SQLite online backup](https://www.sqlite.org/backup.html) により、
更新中の DB ファイルを単純コピーする代わりに、整合したスナップショットを取得します。
コミット済み WAL は含み、未コミットのデータやアプリ内の未保存バッファは含みません。

**DB ごとに整合しますが、Core と Reports の2つを同時点に固定する処理ではありません。**
順番に取得し、Reports は Core より遅れている場合があります。
これはデータの証跡であり、実行ファイル・設定・プラグイン・スクリーンショットも含む
ManicTime 一式のバックアップではありません。

state の実行記録に、取得開始・完了 UTC 時刻、入力パス、ファイルサイズ、SHA-256、ツール版、
SQLite journal mode、取得方法を記録し、`PRAGMA quick_check` で DB の整合性を確認します。
入力は読み取り専用で開き、削除・上書き・vacuum・取得済み行の間引きを行いません。
保存先の最新 Raw は検証後に置換します。集計・内部テーブルを含む全テーブルが Raw に残ります。

Raw は **通常時に1世代、通常の更新中には新旧2世代**を保持します。
例えば両 DB の合計が754 MBなら、通常時は約754 MB、新旧が同程度のサイズなら更新中は約1.5 GBです。
別途、CSV の更新用一時領域が必要です。容量は DB 自体の成長に従い、実行回数に比例して増えません。
旧 DB は同じファイルシステム上で一時退避し、state に3世代目のコピーを作りません。

失敗・停止した更新候補は、復旧成功後に削除します。失敗理由・取得来歴は state に残します。
復旧・整理に失敗した場合は作業用データを残し、次の取得より先に復旧を必要とします。

### 活動件数の減少による停止

Raw・CSV を置換する前に、直前の成功時と `Ar_Activity` の行数を比較します。
活動全体と、既存の `ReportId` ごとのタイムラインについて、既定では **10％を超えて減った場合**に停止します。
空でなかったタイムラインの消失は100％減として扱い、他のタイムラインが増えていても検出します。
通常実行と `ingest --dry-run` は同じ検査を行います。停止する dry-run は書き込まずエラーを返します。
停止する通常実行は前回データを保持し、復旧後に候補を削除して、件数・理由を state に記録します。

`max_activity_drop_percent` は共通設定です。プロファイル内の設定ではありません。
`0` はすべての件数減少を停止し、`100` は全件消失を含むすべての減少を許容します。
しきい値と同率の減少は許容します。これは件数の検査であり、過去の全行の包含を証明するものではありません。
少量の欠落、同件数での入れ替わり、徐々に進む欠落などは通過し得ます。修正・削除自体をエラーにはしません。

意図した減少なら入力を確認し、必要に応じて独立したバックアップを保持したうえで、
その実行だけに明示的な追加設定を指定できます。

```yaml
schema_version: "2.1.0"
max_activity_drop_percent: 100
```

```console
tkn-manictime-pipeline --config "C:\path\to\approved-reduction.yaml" ingest --dry-run
tkn-manictime-pipeline --config "C:\path\to\approved-reduction.yaml" ingest
```

通常の設定からプロファイル・保存先の設定を読み込める状態で使います。
この追加設定を付けない次回実行では通常のしきい値に戻ります。
自動で条件を緩めて再試行する処理や、手編集・未管理ファイルを強制上書きするオプションはありません。

### 抽出対象と解釈

テーブル名・列名は原名を使い、アイコン、source ID、変更 sequence、内部 metadata を含む全列を保存します。
manifest に SQL 定義・列型・主キーを記録します。
Reports DB の非集計 `Ar_*` テーブルを対象とし、末尾が `ByHour`・`ByDay`・`ByYear`
のテーブルと `Ar_TimelineSummary` は Raw のみに保存します。
`Ar_*` 以外の内部テーブルも Raw のみです。

最低限、次のテーブルと主キーを必要とします。

| テーブル           | 主キー                   | 関係・用途                     |
| ------------------ | ------------------------ | ------------------------------ |
| `Ar_Activity`    | `ReportId, ActivityId` | 活動区間と元の文字列           |
| `Ar_Timeline`    | `ReportId`             | 各タイムラインを schema で解釈 |
| `Ar_Group`       | `ReportId, GroupId`    | 活動から両列を使って結合       |
| `Ar_CommonGroup` | `CommonId`             | 共通グループの定義             |

タグ・グループリストの活動では `GroupId` が null の場合があります。
`Ar_GroupList` と `Ar_GroupListItem` も保存して関係を維持します。
タイムラインの ReportId は環境をまたいで固定値と仮定しないでください。
異なるタイムラインの活動時間は重なるため、単純合計は PC 利用時間になりません。

活動は `StartLocalTime` の年月で分割します。
日付・月をまたぐ区間も分割しません。行のない月には活動 CSV を作らず、
空の関連テーブルにはヘッダーのみの CSV を作ります。
`*UtcTime` は UTC、`*LocalTime` は記録された現地時刻として、元の文字列を保持します。
推測した timezone や offset を加えません。
manifest の時刻は offset 付き ISO 8601 UTC です。

CSV は BOM なし UTF-8、カンマ区切り、LF、ヘッダー付きで、カンマ・引用符・改行を CSV の引用規則で保存します。
数値は入力の表現を使用します。SQL null は `\N`、BLOB は `\B` の後に base64、
先頭がバックスラッシュの文字列は先頭にバックスラッシュを1つ追加します。
空文字・null・バイナリ・マーカーと同じ文字列を区別できます。
LF は CSV レコードの区切りです。元の値の中にある CR・LF・CRLF は、引用したうえで保持します。
`manictime_pipeline.export.decode_cell` でマーカーを復号できます。
タイトルは原文を保持するため、表計算ソフトではデータ・文字列として取り込んでください。
数式と解釈され得る文字列もそのまま残っています。

### 差分更新と識別子

対象全行を主キー順に読み、月・テーブルごとの SHA-256 を計算します。
更新前に、前回公開した CSV のハッシュも検証します。
ID や変更 sequence の最大値だけに頼らず、
古い行の修正・削除、遅れて追加された行、アイコンの変更、別月への活動移動も検出します。

書き込むのは変更のあったパーティションだけです。
更新された月は同じ CSV パスをその月の全行で置換します。未変更の CSV はパス・更新日時を保持します。
変更されたテーブルは2回目の読み込みで対象パーティションを出力します。
活動件数に比例して全行をメモリに保持する方式ではありません。
**出力は差分更新ですが、比較のための全件読み取りは毎回行います。**

state/current.json が参照する完全な実行記録がチェックポイントです。dataset UUID を次回以降も維持し、
dataset UUID + テーブル名 + 入力の主キーでレコードを識別します。
schema の変更では該当テーブルの出力を新版にします。
必須テーブル・主キーの欠落、主キーのない抽出テーブル、不正な活動日時では公開を中止します。

`verify` は pointer/manifest のハッシュ、現在参照する全 CSV のハッシュ・ヘッダー・列数・行数、
最新 Raw の両 DB のハッシュと整合性、Raw Reports から再計算したパーティションの一致を確認します。
更新され続ける稼働 DB や、現在参照していない過去 run すべてを監査するコマンドではありません。

### 失敗時と再実行

候補 DB は `<raw_path>/<device_id>/.<run-id>.partial/new/` に取得して検証します。
取得済みの DB は SQLite の WAL・共有メモリファイルを新規作成せずに読みます。
稼働中の入力には通常の SQLite ロックを使い、コミット済み WAL の内容も取得します。
保存済み Raw の横に想定外の SQLite 補助ファイルがあれば、無視・削除せず検証を停止します。
変更 CSV は `state/<profile>/transactions/<run-id>/new/` に生成・検証し、旧 CSV のコピーは同じ実行の `old/` に置きます。
来歴と復旧手順は state に保存し、Raw・CSV 保存先に JSON は置きません。

準備後、旧 DB を Raw 作業用フォルダの `old/` へ退避し、候補 DB を固定パスへ移動します。
CSV は保存先と同じフォルダの一時ファイルを経由して置換し、state と CSV が別ドライブでも対応します。
Raw・CSV の両方を検証してから実行記録を保存し、state の `current.json` を確定します。
旧 DB、復旧用 CSV、作業用データは成功確定後に削除します。

通常のエラーでは前回の Raw・CSV へ戻します。ファイルが開かれている、手編集されているなどの理由で
戻せない場合は、復旧用データと手順を state に残します。プロセスの強制停止でも手順が残ります。
次のコマンドで復旧します。

```console
tkn-manictime-pipeline recover --dry-run
tkn-manictime-pipeline recover
tkn-manictime-pipeline verify
```

`recover --dry-run` は保留中の実行を一覧し、書き込みません。
`recover` は未確定の変更を元に戻し、チェックポイントが確定済みなら一時データだけを整理します。
ハッシュを検査し、無関係な手編集を上書きしません。通常の `ingest` も次の実行を始める前に復旧します。
読み取り専用の ingest と verify は、復旧が保留中なら停止します。抽出処理の途中再開は行いません。

state への書き込みは公開の必須条件です。最初の実行記録を書けなければ Raw・CSV の保存前に停止します。
チェックポイントが参照する確定済み実行記録は変更しません。
未確定・中断した実行の記録は復旧時に failed とする場合があります。
成功確定の基準は実行記録内の status 単独ではなく `current.json` です。
失敗した候補は復旧後に整理し、直前に成功した Raw を保持します。過去の実行記録は state に残します。
設定・事前検証のエラーは標準エラーに表示します。

プロセス終了時には OS がロックを解放し、state 内の小さいロックファイルは残ります。
同期ソフト、手編集、任意の CSV 読み込み側はこのロックに参加しません。
固定パスの複数ファイルを一括で切り替える保証はありません。
失敗・中断後は復旧が完了するまで Raw・CSV を利用しないでください。
ストレージ障害などで復旧できない場合は、影響を受けたファイル・state をバックアップから戻す必要があります。

管理対象 CSV の手編集・欠損、デバイス別 CSV フォルダ内にあるすべての未管理 CSV は ingest と verify を停止させます。
未管理データは別の場所に保全し、管理対象の破損は元に戻してください。強制上書きオプションはありません。
入力 DB は常に読み取り専用です。

dry-run は稼働中 Reports DB を1つの読み取りトランザクションで読みます。
rollback journal 方式では、この間アプリ側の書き込みが短時間待つ可能性があります。
backup と整合性検査は省くため、出力先の権限・空き容量・backup の成功までは保証しません。
SQLite 自身は通常のロック・共有メモリ管理を行いますが、CLI は入力への書き込み SQL を実行しません。

## 保守・開発・検証

コード、同梱リソース、依存関係の更新後は再インストールします。

```console
cd "C:\path\to\tkn_manictime_data_pipeline"
uv tool install . --reinstall
tkn-manictime-pipeline --version
```

開発時は次を使います。

```console
cd "C:\path\to\tkn_manictime_data_pipeline"
uv sync --group dev
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv build
```

テストは架空の SQLite DB で、WAL 保存、元 DB の保全、過去行の変更・削除、冪等性、
schema 変更、出力破損、Raw 移動間・CSV 差し替え中・確定後のプロセス強制終了、復旧、
Raw 保持容量、活動件数減少の停止、未管理ファイルとの衝突、
state の書き込み失敗、ロック、設定階層の厳密な検証、
日本語・BLOB・NULL の CSV、標準エラーと JSON の分離を確認します。
CLI・設定、SQLite アクセス、CSV の逐次処理、パイプラインの公開、ファイル・ログ処理に責務を分離しています。
実データや個人情報をテストには含めません。
[小さな出力形式の例](docs/manifest.example.json)も参照できます。

既存 Itadaki パイプラインの src layout、uv 配布、YAML プロファイル、
アプリ所有の state、通常実行で書き込む／dry-run で確認する契約を参考にしています。
ManicTime は1つの DB を更新し続けるため、スナップショット保存と過去修正を検出する
パーティション更新を採用し、Itadaki の取得済み入力を削除する処理は引き継いでいません。
