# ManicTime Data Pipeline

[English](README.md)

ManicTime の SQLite DB を変更しない Raw として保存し、再利用できる CSV を抽出する CLI です。
初回は全期間の活動と関連テーブルを出力し、次回からは変更のあった月・テーブルだけを更新します。
過去の Raw と CSV の版も残ります。
v0.2 の範囲は取得・抽出・実行記録・検証です。HTML レポート、分類ルール、生成 AI の助言、
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

| 保存先                                                                | 役割                                       |
| --------------------------------------------------------------------- | ------------------------------------------ |
| `<raw_path>/<device_id>/<run-id>/`                                          | 両 DB のスナップショットと`capture.json` |
| `<processed_data_path>/<device_id>/pipeline-v1/current.json`        | 現在の完全なデータセットへの入口           |
| `.../pipeline-v1/runs/<run-id>/manifest.json`                       | 全パーティションの一覧、schema、来歴       |
| `.../pipeline-v1/runs/<run-id>/Ar_Activity/YYYY/MM.csv`             | 変更のあった活動月の新版                   |
| `.../pipeline-v1/runs/<run-id>/<table>/all.csv`                     | 変更のあった関連・メタデータテーブルの新版 |
| `~/.tkn/manictime_data_pipeline/state/<profile>/runs/<run-id>.json` | 実行状態、件数、失敗の記録                 |

`current.json`、そこに記載された manifest の順に読み、
**`manifest.artifacts` に列挙されたパスだけ**を利用します。
パスは `pipeline-v1` 基準の相対パスです。
未変更のファイルは以前の run を参照するため、
`runs` 以下の CSV を再帰的にすべて結合すると旧版まで二重計上されます。

例えば、標準ライブラリだけで現在の活動 CSV を一覧できます。

```python
import json
from pathlib import Path

root = Path("C:/path/to/ManicTime/Example Current PC/pipeline-v1")
pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
manifest = json.loads((root / pointer["manifest"]).read_text(encoding="utf-8"))
activity_csvs = [
    root / item["path"] for item in manifest["artifacts"].values() if item["table"] == "Ar_Activity"
]
```

`pipeline-v1` の外にある既存の PowerShell 出力はそのまま残ります。
自動移行・削除は行いません。

## コマンド一覧

| 目的                                       | コマンド                    |
| ------------------------------------------ | --------------------------- |
| 編集済み内容を上書きせずユーザー設定を作成 | `config init [--dry-run]` |
| 解決後の値と、その値を決めた設定元を確認   | `config show`             |
| 入力 schema・件数・活動期間を確認          | `inspect`                 |
| Raw 保存と CSV 差分更新                    | `ingest [--dry-run]`      |
| 最新 CSV と対応する Raw を検証             | `verify`                  |

共通オプションは `--config PATH`、`--profile NAME`、
`-q/--quiet`、`-v/--verbose` です。
コマンドの前後に指定できます。quiet と verbose は同時指定できません。
`config init` は常にユーザー設定を対象とし、`--config`・`--profile` を受け付けません。
同じテンプレートがあれば unchanged、編集済みなら保持したうえでエラーになります。

終了コードは成功 0、設定・処理エラー 1、引数の使用誤り 2、中断 130 です。
quiet でも標準出力の結果 JSON は表示されます。verbose ではエラーの traceback も表示します。
対応した対話端末でのみ ANSI 色を使い、`NO_COLOR` に従います。

`ingest` の created / updated / unchanged は **CSV データセット**に対する状態です。
unchanged でも新しい Raw と実行 manifest は保存します。
removed の件数は最新 manifest から外れたパーティション数であり、ファイル削除数ではありません。

## 設定の詳細

各 YAML は `schema_version: "2.0.0"` を持ちます。2.0.x に対応し、
未対応の major/minor、キーの重複・未知キー、型の不一致はエラーにします。
各設定元をマージ前に検証するため、上位設定で下位設定の誤りを隠すことはできません。

優先順位は built-in → ユーザー設定 → 実行時の `.tkn/config.yaml`
→ `--config` → CLI でのプロファイル選択です。
profiles は名前、次にプロパティ単位でマージします。
`config show` には各入力の schema version、内部 schema version、解決後のパス、
値ごとの設定元を表示します。読み込みで設定を書き換えません。

| キー | 意味 |
| --- | --- |
| `default_profile` | `--profile` 省略時の対象 |
| `raw_path` | DB コピーの保存先の親フォルダ。既定値は `~/.tkn/manictime_data_pipeline/data/raw` |
| `processed_data_path` | CSV データ出力先の親フォルダ。既定値は `~/.tkn/manictime_data_pipeline/data/csv` |
| `state_path` | 実行記録の保存先。既定値は `~/.tkn/manictime_data_pipeline/state` |
| `backup_timeout_seconds` | DB ごとの backup の制限秒数。既定 300、整数 1～86400 |
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

この例の保存先は `C:/path/to/archive/Example Historical PC/<run-id>/` と
`C:/path/to/csv/Example Historical PC/pipeline-v1/` です。追加の `Raw` フォルダは作りません。
`device_id` を変更すると保存先も変わるため、既存データの表示名変更には使用しないでください。

`~` は実行ユーザーのホームに展開します。
相対パスは、どの設定元でも YAML の場所ではなく実行時のカレントディレクトリを基準にします。
インストール済み CLI がリポジトリ内の設定を読むのは、そこで実行した場合か、
`--config` で指定した場合です。実設定、DB、実行結果は Git 管理外に置きます。

過去 PCの DB には、別名のプロファイルと異なる `device_id` を追加できます。
元のアーカイブを入力にする場合も、Raw 出力は別フォルダに指定してください。
1回の実行では選択した1プロファイルだけを処理し、他の入力を自動取得しません。
公開済みデータセットは、解決後の入力パス・Raw ルート・device ID と結び付きます。
これらの変更は、後述の v0.1 から v0.2 への Raw 配置変更を除き拒否します。
それ以外の保存先移行は別の明示的な手順として扱います。

### Windows Task Scheduler

日次・週次のタスクでインストール済み実行ファイルを呼び出します。
`Get-Command tkn-manictime-pipeline` で取得した絶対パスをプログラムに指定し、
次を引数にします。

```console
--config "C:\path\to\config.yaml" --profile current-pc ingest
```

定期実行用設定のデータパスには絶対パスを推奨します。
実行アカウントのホームが設定・state の基準になります。
Task Scheduler は重複起動しない設定にし、CLI でも Raw と出力のルートを OS のファイルロックで保護します。
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

取得開始・完了 UTC 時刻、入力パス、ファイルサイズ、SHA-256、ツール版、
SQLite journal mode、取得方法を記録し、`PRAGMA quick_check` で DB の整合性を確認します。
入力は読み取り専用で開き、削除・上書き・vacuum・取得済み行の間引きを行いません。
スナップショットも上書きしません。集計テーブルや内部テーブルを含む全テーブルが Raw に残ります。

容量は1回ごとに両 DB の合計サイズと変更 CSV 分だけ増えます。
例えば DB 合計 754 MB なら、日次30回で Raw は約23 GB 増えます。
v0.2 に保持期限・cleanup コマンドはありません。CSV の旧版も保持します。
最新 manifest が過去 run 内の未変更パーティションを参照する場合もあります。

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
更新された月は、その月全体の置換版であり、追記用の差分行ファイルではありません。
変更されたテーブルは2回目の読み込みで対象パーティションを出力します。
活動件数に比例して全行をメモリに保持する方式ではありません。
**出力は差分更新ですが、比較のための全件読み取りは毎回行います。**

完全な manifest がチェックポイントです。生成した dataset UUID を次回以降も維持し、
dataset UUID + テーブル名 + 入力の主キーでレコードを識別します。
schema の変更では該当テーブルの出力を新版にします。
必須テーブル・主キーの欠落、主キーのない抽出テーブル、不正な活動日時では公開を中止します。

`verify` は pointer/manifest のハッシュ、現在参照する全 CSV のハッシュ・ヘッダー・列数・行数、
最新 Raw の両 DB のハッシュと整合性、Raw Reports から再計算したパーティションの一致を確認します。
更新され続ける稼働 DB や、現在参照していない過去 run すべてを監査するコマンドではありません。

### 失敗時と再実行

Raw を新しい `.partial` フォルダに保存し、検証後に名前を確定します。
次に CSV と manifest を仮配置・検証し、最後にだけ `current.json` を原子的に置換します。
前回の Raw・CSV は残ります。この境界により、manifest に従って読む利用者へ
途中までの出力を公開しません。

run 作成後の失敗は `state/<profile>/runs` に記録します。
不完全な Raw・CSV フォルダは診断用に残し、抽出が失敗しても取得済み Raw は保持します。
再実行は新しい run として直前の成功状態と比較します。
v0.2 は失敗 run の途中再開や残骸の自動削除を行いません。
設定・事前検証でのエラーは run 作成前に標準エラーへ表示します。
運用 state の最終記録に失敗しても公開済み manifest を正とし、警告を表示します。

終了・プロセス強制停止時には OS がロックを解放します。小さいロックファイル自体は残ります。
強制停止では運用記録が running のまま残る場合があります。
成功の基準は `current.json` が参照する manifest です。
公開済み CSV が手編集・欠損していたら ingest と verify は停止します。
バックアップから復元するか、別の processed root へ再抽出してください。強制上書きオプションはありません。
外部の手編集・同期ソフトによる変更は CLI のプロセスロックの保護対象外です。

dry-run は稼働中 Reports DB を1つの読み取りトランザクションで読みます。
rollback journal 方式では、この間アプリ側の書き込みが短時間待つ可能性があります。
backup と整合性検査は省くため、出力先の権限・空き容量・backup の成功までは保証しません。
SQLite 自身は通常のロック・共有メモリ管理を行いますが、CLI は入力への書き込み SQL を実行しません。

## 保守・開発・検証

### v0.1 からの更新

CLI は v0.2.0、設定スキーマは 2.0.0 です。出力 manifest のスキーマは 1.0.0 を継続し、
既存の `csv_contract.encoding` に CSV の形式を記録します。
設定 1.0.x は意味を変えて読み込まず、移行案内を付けてエラーにします。

1. 旧版の `config show` に表示された各設定ファイルをバックアップします。未編集の旧サンプルは
   バックアップ後に新しい同梱テンプレートへ置き換えられます。編集済みの値は保持してください。
2. 各設定の `schema_version` を `"2.0.0"` にします。`source_path`、`device_id`、
   `processed_data_path`、`state_path` は意図した値を保持します。共通 CSV 親フォルダの意味は変わりません。
3. 旧 `profiles.<name>.raw_path` はデバイス別のアーカイブ先でした。末尾のフォルダが `device_id` なら、
   新しい `raw_path` はその親を指定します。共通設定・プロファイル別指定のどちらでも同じ規則です。
   Raw・CSV とも親フォルダを指定し、CLI が `device_id` を一度だけ追加します。
4. 再インストール後に `config show`、`ingest --dry-run`、`ingest`、`verify` の順で確認します。

標準の旧配置では、既存 Raw は `<raw_path>/<device_id>/Raw/<run-id>/` に残し、
次回取得分から `<raw_path>/<device_id>/<run-id>/` に保存します。Raw を移動・削除しません。
最初の新しい ingest の前でも、旧データセットを verify できます。
BOM を取り除くと全ファイルのハッシュが変わるため、初回更新では存在する全 CSV をBOMなしの新版にします。
旧 BOM 付き CSV と manifest は保持し、dataset ID も維持します。
以後は未変更の BOM なし CSV を再利用します。更新に失敗した場合は直前のデータセットが残ります。
旧 Raw 保存先の末尾が `device_id` でない場合、この配置変更は自動適用できません。
単に設定パスを変えて既存データが引き継がれるとは考えず、個別に移行してください。

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
schema 変更、出力破損、公開前の障害、ロック、設定階層の厳密な検証、
日本語・BLOB・NULL の CSV、標準エラーと JSON の分離を確認します。
CLI・設定、SQLite アクセス、CSV の逐次処理、パイプラインの公開、ファイル・ログ処理に責務を分離しています。
実データや個人情報をテストには含めません。
[小さな出力形式の例](docs/manifest.example.json)も参照できます。

既存 Itadaki パイプラインの src layout、uv 配布、YAML プロファイル、
アプリ所有の state、通常実行で書き込む／dry-run で確認する契約を参考にしています。
ManicTime は1つの DB を更新し続けるため、スナップショット保存と過去修正を検出する
パーティション更新を採用し、Itadaki の取得済み入力を削除する処理は引き継いでいません。
