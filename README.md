# Claude Code 作業履歴ビューア

Claude Code が保存している作業履歴(`~/.claude/projects/**/*.jsonl`)を読み込み、
セッションごとの作業をブラウザで可視化するローカルツール。

## はじめかた(インストールして使うまで)

自分のパソコンの履歴だけを見るなら、これだけで使える。外部のサービスやアカウントは要らず、
履歴は **自分のパソコンの外へ送られない**(画面サーバーは `127.0.0.1` だけで待ち受ける)。

### 必要なもの

- Mac または Windows に **Python 3.9 以上**(追加のライブラリは不要)
- **Claude Code** を使っていて、作業履歴(`~/.claude/projects/`)がある

Python が入っているかは、ターミナル(Windows は PowerShell)で確認する:

```bash
python3 --version      # Windows は py --version
```

入っていなければ [python.org](https://www.python.org/downloads/) からインストールする。

### 1. ダウンロードする

**git を使える場合**(ホーム直下に置く):

```bash
cd ~
git clone https://github.com/sato0608/claude_code_history_viewer.git
```

**git が無い場合**: このページ右上の緑の「Code」→「Download ZIP」で取得し、展開したフォルダを
ホーム直下(`~/claude_code_history_viewer`)に置く。

> Mac では、デスクトップ・書類・ダウンロードの中に置くと、自動起動したときに読み込みを拒否されることがある。ホーム直下を勧める。

### 2. 起動して開く

```bash
cd ~/claude_code_history_viewer
python3 server.py                      # Windows は py server.py
```

ブラウザで **http://127.0.0.1:8765** を開く。履歴が読み込まれ、画面が表示されれば成功。
止めるときはターミナルで Ctrl+C。Claude Code で作業を続けると、画面は再読み込みなしで更新される。

### 3. (任意)Claude Code を起動するたびに自動で起動する

```bash
cd ~/claude_code_history_viewer
python3 setup_autostart.py install             # Windows は py setup_autostart.py install
```

次に Claude Code を起動したときから、画面サーバーが裏で自動的に起動する。
解除は `python3 setup_autostart.py uninstall`。詳しくは下の「Claude Code の起動時に自動で起動する」。

### 4. 更新する

```bash
cd ~/claude_code_history_viewer
git pull
```

自動起動を登録している場合は、更新後に `python3 setup_autostart.py install` をもう一度実行するか、
動いているサーバーを止めて起動し直すと反映される。

### うまくいかないとき

| 症状 | 確認すること |
|---|---|
| 画面に「表示できるセッションがありません」と出る | Claude Code を使った履歴が `~/.claude/projects/` にあるか。別の場所なら `--claude-dir` で指定する |
| `Address already in use` と出る | すでに起動している。ブラウザで http://127.0.0.1:8765 を開くか、`--port 8766` で別のポートにする |
| `python3` が見つからない(Windows) | `py` で試す。それでもだめなら Python を入れ直し、インストール時に「Add Python to PATH」にチェックする |
| 短い作業が細切れに並んで見づらい | セッション画面の「極短の作業をまとめる」「隠す設定」で調整できる(下の「画面」を参照) |

---

## 2つの使い方

| | ローカル表示 | 外出先から表示 |
|---|---|---|
| 動かす場所 | Mac / Windows だけ | Mac / Windows(送信側)+ 常時稼働サーバー(表示側) |
| Mac の電源を切ったとき | 見られない | 最後に同期した内容を見られる |
| 必要なもの | Python 3 | Python 3、HTTPS で公開できる常時稼働サーバー |

Python 3.9 以上の標準ライブラリだけで動く(追加インストール不要)。
元の履歴ファイルは読むだけで、書き換えない。

## Claude Code の起動時に自動で起動する(Mac / Windows)

一度登録すれば、Claude Code を起動するたびに、画面サーバー(と、設定していれば同期)が動いているか確認し、
止まっていれば裏で起動する。動いていれば何もしない(二重には起動しない)。

```bash
# Mac
cd ~/claude_code_history_viewer
python3 setup_autostart.py install                                        # 画面サーバーだけ
python3 setup_autostart.py install --hub https://<サーバー> --token <PUSH_TOKEN>   # 外出先表示の同期も

# Windows(PowerShell。python の代わりに py でもよい)
cd $HOME\claude_code_history_viewer
python setup_autostart.py install
python setup_autostart.py install --hub https://<サーバー> --token <PUSH_TOKEN>
```

- 登録後、次に Claude Code を起動したときから有効になる。画面は http://127.0.0.1:8765 。
- `install` を再実行すると、**指定した項目だけ** 設定が変わる(前回の同期・通知などの設定は残る)。作り直すときは `--reset`、
  同期や通知の設定を消すときは `--no-hub` / `--no-ntfy`。

### Claude Code の起動時に画面を自動で開く

```bash
python3 setup_autostart.py install --open app        # インストールしたアプリで開く(見つからなければブラウザ)
python3 setup_autostart.py install --open browser    # いつものブラウザで開く
python3 setup_autostart.py install --open off        # 開かない(既定)
```

- 画面が **どこにも開いていないときだけ** 開く(ブラウザのタブ・アプリの画面が1つでも開いていれば何もしない)。
  Claude Code を何回起動しても画面は増えない。開いているかは、画面サーバーにつながっているページの数で判断する。
- `--open app` で試す順番:
  - Mac: ① 名前でアプリを開く(`open -a "Claude Code 作業履歴"`。Chrome などで「アプリとしてインストール」したときの名前)
    → ② Chrome の「画面だけの窓」(`--app`)→ ③ いつものブラウザ
  - Windows: ① Chrome か Edge の「画面だけの窓」(`--app`)→ ② いつものブラウザ
- アプリの名前が違う場合(Safari の「Dock に追加」で名前を変えた場合など)は `--app-name "<名前>"` で指定する。
- 開く処理の記録は `~/.claude-code-program/logs/open.log` に出る。
- 状態の確認: `python3 setup_autostart.py status` / 解除: `python3 setup_autostart.py uninstall`
  (`--purge` を付けるとトークンを含む設定とログも消す)。
- 登録内容: `~/.claude/settings.json` の `hooks.SessionStart` に、`autostart.py` を呼ぶ設定を1つ追加する。
  既存の設定は残し、書き換える前に `settings.json.bak-日時` としてバックアップを作る。
  URL とトークンは `~/.claude-code-program/config.json`、ログは `~/.claude-code-program/logs/` に置く。
- 登録したときの Python を使って起動する。フォルダを移動したり Python を入れ替えたりしたら、`install` をやり直す。
- 自動起動するのは、そのパソコンで Claude Code を起動したときだけ(クラウドで動かす Claude Code では起動しない)。
  起動したプログラムは Claude Code を終了しても動き続け、パソコンのシャットダウンで止まる。

## ローカル表示(手動で起動する場合)

```bash
cd ~/claude_code_history_viewer
python3 server.py            # → http://127.0.0.1:8765 をブラウザで開く
```

| オプション | 既定値 | 内容 |
|---|---|---|
| `--port` | 8765 | 待ち受けポート |
| `--host` | 127.0.0.1 | 待ち受けアドレス。履歴には会話内容が含まれるので、外部に公開するなら `--password` も付ける |
| `--password` | なし | 設定するとログインが必要になる(環境変数 `VIEWER_PASSWORD` でも可) |
| `--claude-dir` | `~/.claude` | Claude Code の設定ディレクトリ |
| `--interval` | 1.5 | 履歴ファイルを確認する間隔(秒) |

## 外出先から表示(Mac を閉じても見る)

```
Mac(Claude Code の履歴) ──sync_client.py が要約を HTTPS で送信──▶ 常時稼働サーバー(server.py --mode hub)◀── スマホのブラウザ(パスワードでログイン)
```

- Mac が起きている間は、数秒ごとに変化分を送る。スマホの画面もリアルタイムで更新される。
- Mac がスリープ・電源オフになると送信が止まる。サーバーは最後に受け取った内容を表示し続け、
  2分間届かなければ右上に「Mac未接続」と出し、作業中/入力待ちだったセッションの状態を「不明(Mac未接続)」にする。
- サーバーには会話内容(発言・会話ログ・ツールの出力の一部)が保存される。要約だけにしたい場合は送信側に `--no-logs` を付ける(会話ログを送らない)。

### 1. 合言葉を2つ決める

| 名前 | 用途 |
|---|---|
| `VIEWER_PASSWORD` | スマホからログインするときのパスワード |
| `PUSH_TOKEN` | Mac がサーバーへ送るときの鍵(16文字以上) |

ランダムな値は Mac のターミナルで作れる: `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`

### 2. 常時稼働サーバーで hub を動かす

条件: Python 3.9 以上(または Docker)が動き、**HTTPS** でインターネットに公開できること。
HTTP のままだとパスワードと会話内容が暗号化されずに流れるため、送信側は既定で `http://` を拒否する。

- **Docker が使えるホスティングサービスの場合**: このフォルダの `Dockerfile` を使う。
  環境変数 `VIEWER_PASSWORD`・`PUSH_TOKEN` を設定する。待ち受けポートは環境変数 `PORT`(無ければ 8765)。
  受け取ったデータは `/data/hub_data.json` に保存されるので、サービス側で `/data` に永続ディスクを付ける。
  永続ディスクが無いとサーバーの再起動でデータが消える(Mac が次に起動したとき全件が自動で送り直される)。
- **自分で管理するサーバー(VPS・ラズパイ等)の場合**:
  ```bash
  VIEWER_PASSWORD=... PUSH_TOKEN=... python3 server.py --mode hub --port 8765 --secure-cookie
  ```
  その前段に HTTPS を受け持つ中継サーバー(Caddy、nginx など)を置き、`127.0.0.1:8765` へ転送する。

動作確認: `https://<サーバー>/healthz` が `ok` を返し、`https://<サーバー>/` でログイン画面が出ればよい。

hub モードのオプション: `--data-file`(保存先、既定 `hub_data.json`、環境変数 `DATA_FILE`)、
`--stale-sec`(この秒数届かなければ未接続とみなす、既定 120)、`--secure-cookie`(HTTPS 公開時に Cookie へ Secure を付ける。
中継サーバーが `X-Forwarded-Proto: https` を渡す場合は自動で付く)。

### 3. Mac / Windows から送る

まず手動で試す(Windows では `set PUSH_TOKEN=...` のあと `python sync_client.py ...`):

```bash
cd ~/claude_code_history_viewer
PUSH_TOKEN=... python3 sync_client.py --hub https://<サーバー>
```

「〇 セッションを送信しました」と出れば成功。Ctrl+C で止める。
問題なければ、上の「Claude Code の起動時に自動で起動する」の手順で `--hub` と `--token` を付けて登録する。
動作の記録は `~/.claude-code-program/logs/sync.log` に出る。

注意:
- フォルダはホーム直下(Mac は `~/claude_code_history_viewer`、Windows は `C:\Users\<名前>\claude_code_history_viewer`)に置くことを勧める。
  macOS はデスクトップ・書類・ダウンロード・外付けドライブをプライバシー保護の対象にしているため、
  そこに置くと自動起動したときに読み込みを拒否されることがある。
- python.org からインストールした Python で `CERTIFICATE_VERIFY_FAILED` が出る場合は、
  `/Applications/Python 3.x/Install Certificates.command` を実行する。

`sync_client.py` のオプション: `--interval`(確認間隔、既定5秒)、`--name`(画面に出す送信元の名前、既定はホスト名)、
`--no-logs`(会話ログを送らない)。

## 画面

| ビュー | 内容 |
|---|---|
| ダッシュボード | セッション数・作業中の数・直近7日の作業時間/コミット/課題・トークン数、日別作業時間(作業種別ごとの積み上げ)、ツール使用回数、プロジェクト別作業時間、稼働中/最近のセッション、最近の課題 |
| セッション | 3つの表示を切り替える。**時間帯**=週単位で、1日を時刻の縦軸に並べる(帯=作業区間)/ **カレンダー**=月単位のマス目で、日ごとのプロジェクト別の件数・作業時間と日別の積み上げグラフを出す(日をクリックでその日の一覧、ダブルクリックでその週の時間帯表示)/ **一覧**=表。状態での絞り込み、色分けの切り替え(作業種別/プロジェクト/状態/ブランチ/セッション)、拡大縮小(±ボタン、Ctrl+ホイール) |
| タスク | Claude が `TaskCreate`/`TaskUpdate`・`TodoWrite` で管理したタスクを、未着手/進行中/完了の3列で表示 |
| 課題 | ツールの実行エラー(`is_error`)、ユーザーによる中断、APIエラーの一覧 |
| 変更履歴 | コミット一覧と、Edit/Write 等で変更したファイル一覧 |
| レポート | 日報・週報を自動で作る(概要、日別・プロジェクト別の作業時間、セッションごとの依頼・完了タスク・コミット・編集ファイル・課題、コミット一覧)。Markdown でコピー、または .md ファイルで保存できる。依頼内容・編集ファイルは表示を切り替えられる |

帯や行をクリックすると右側に詳細パネルが開き、状態・概要・タスク・コミット・変更ファイル・発言・ツール使用回数・課題・会話ログを確認できる。
上部のプロジェクト選択で、そのプロジェクトの作業だけに全ビュー(レポートを含む)を絞り込める(選んだ内容はブラウザに記憶される)。
上部の検索欄はタイトル・発言・プロジェクト・ブランチを対象に全ビューを絞り込む。

### 極短セッションの扱い

数秒で終わる動作確認や自動処理のセッションが多いと、カレンダーが細切れになる。そのため次の機能がある。

- **まとめる**: 時間帯表示で、極短の作業(既定は2分未満)を、同じプロジェクトで近いもの(既定は3分以内)ごとに1本の帯にする。
  帯をクリックすると、まとめた全件を右のパネルに一覧表示する。基準と間隔はツールバーで変えられる。
- **隠す**: フォルダ名が `memory` のもの、または3件以上あって全部が極短のプロジェクトは、極短セッションを自動で隠す。
  右上の「隠す設定」でプロジェクトごとに切り替えられ、「隠した極短を表示」で一時的に全部見られる。

## 通知

セッションが「作業中 → 入力待ち」「作業中 → エラー」「作業中 → 終了(プロセス情報が無く、応答の完了を履歴から判定した場合)」に
変わったときに知らせる。起動直後に読み込んだ分や、自分で中断した場合は知らせない。

### A. ブラウザの通知(ページを開いている間)

- 右上の「🔔 オフ」を押して、ブラウザの確認で「許可」を選ぶと「🔔 オン」になる。
- ページを開いている間(裏のタブや最小化した状態でも可)に通知が出る。前面で操作している最中は出さない。
- 通知を押すと、そのセッションの詳細が開く。
- 条件: https、または `http://127.0.0.1`(自分の PC)で開いていること。ブラウザを閉じていると届かない。
  スマホのブラウザは裏に回ったページを止めることがあるので、スマホで確実に受けたい場合は B を使う。
- 「🔔 ブロック中」と出る場合は、ブラウザの設定でこのサイトの通知が拒否されている。

### B. ntfy でスマホへプッシュ通知(ページを閉じていても届く)

[ntfy](https://docs.ntfy.sh/) は、HTTP で送った文字をスマホのアプリ(Android / iOS)へ届ける通知サービス。

1. スマホに ntfy アプリを入れ、推測されにくいトピック名(例: `cc-` + ランダムな文字列)を購読する。
   ntfy.sh のトピック名は公開されているので、名前を知っている人は誰でも購読できる。推測されない名前にすること。
2. 通知を送る側を **1か所だけ** 設定する(両方に設定すると同じ通知が2回届く)。
   - Raspberry Pi などの hub を使う場合 → hub に設定する:
     `python3 server.py --mode hub ... --ntfy-url https://ntfy.sh/<トピック名> --public-url https://<hub の URL>`
     (環境変数 `NTFY_URL` / `NTFY_TOKEN` / `PUBLIC_URL` でも可)
   - hub を使わない場合 → PC の自動起動に設定する:
     `python3 setup_autostart.py install --ntfy-url https://ntfy.sh/<トピック名>`
     (すでに画面サーバーが動いていれば、止めてから起動し直すと反映される)
3. 任意のオプション: `--public-url`(通知を押したときに開く URL)、`--ntfy-token`(認証付きの ntfy サーバーを使う場合)、
   `--ntfy-private`(通知にセッション名・プロジェクト名を入れない)。
- 通知の文面(セッション名・プロジェクト名)は ntfy のサーバーを経由する。外部に出したくなければ `--ntfy-private` を付けるか、
  ntfy を自分のサーバー(Raspberry Pi など)で動かす(ntfy はオープンソースで、自分で動かせる)。

## アプリのように使う

ブラウザの「アプリとしてインストール」「ホーム画面に追加」に対応している(manifest と Service Worker を用意している)。
追加すると、ブラウザのタブやアドレスバーの無い独立した画面で開き、Dock・タスクバー・ホーム画面のアイコンから起動できる。

| 端末 | 方法(ブラウザの版によってメニュー名が違うことがある) |
|---|---|
| Mac / Windows の Chrome・Edge | http://127.0.0.1:8765 を開き、アドレスバーのインストールのアイコン、またはメニューの「インストール」系の項目を選ぶ |
| Mac の Safari | 「ファイル」メニュー →「Dock に追加」 |
| iPhone の Safari | hub の https の URL を開き、共有ボタン →「ホーム画面に追加」 |
| Android の Chrome | hub の https の URL を開き、メニュー →「ホーム画面に追加」または「アプリをインストール」 |

- スマホから使うには https の URL(Raspberry Pi + Tailscale の hub など)が必要。
- アプリとして開いた画面でも、画面サーバー(または hub)が動いている必要がある。
- iPhone / iPad では、ブラウザの通知はホーム画面に追加した Web アプリだけが使える(iOS / iPadOS 16.4 以降、WebKit の公表による)。

## リアルタイム更新

サーバーが `--interval` 秒ごとに履歴ファイルの追記分だけを読み込み、表示内容が変わったセッションを
Server-Sent Events(`/api/events`)でブラウザへ送る。ブラウザは再読み込みせずに表示を更新する
(右上が「リアルタイム更新中」/「更新 HH:MM」なら接続中)。会話ログを開いている場合はログも更新される。

## 集計の定義

- **セッションの状態**
  - `~/.claude/sessions/<pid>.json` があり、そのプロセスが生きている場合: `status` が `busy` なら「作業中」、`idle` なら「入力待ち」。
  - そのファイルが無い場合は履歴の末尾から推定する: 最後がユーザーの中断なら「中断」、APIエラーなら「エラー」、
    最後の記録から3分以内で応答の途中なら「作業中(推定)」、それ以外は「終了」。
  - hub モードで Mac からの送信が途絶えた場合、作業中/入力待ちだったものは「不明(Mac未接続)」と表示する。
  - `~/.claude/sessions/` の形式は Claude Code 2.1.288 で確認したもの。他のバージョンで形式が違う場合は、履歴からの推定だけになる。
- **作業区間・作業時間**: ユーザー/Claude の記録の時刻を並べ、間隔が10分以内なら同じ区間とする。作業時間は区間の長さの合計(1区間は最低1分)。
- **作業種別**(推定): ツールの使用回数から決める。編集系(Edit/Write/MultiEdit/NotebookEdit)が読み取り系・実行系の1/3以上なら「実装」、
  そうでなければ実行系(Bash 等)と読み取り系(Read/Grep/Glob/Web 等)の多い方で「実行・検証」か「調査」、ツールを使っていなければ「会話」。
- **トークン**: Claude の応答の `usage` を合計する。1つの応答が複数行に分かれて記録されるため、`message.id` ごとに1回だけ数える。料金は計算しない。
- **コミット**: Bash で `git commit` を実行し、エラー(終了コードが0以外)にならなかったものを数える。
  ハッシュは結果に `[ブランチ ハッシュ]` が出た場合だけ記録する(`-q` 付きなどで出なければ「ハッシュ不明」)。
  ヒアドキュメントの本文や文字列の中の `git commit` は数えない。`git commit ... ; 別のコマンド` のように
  後ろのコマンドが成功すると、コミットが失敗していても数える場合がある。
- **レポートの「編集したファイル」**: Edit/Write 等で、その期間内に編集した記録があるファイル(1ファイルにつき直近100回分の編集時刻を保持)。
- **レポートの「完了したタスク」**: 期間内に最後に更新され、その時点で完了になっているタスク。
- **タイトル**: セッション名(`/rename` 等で付けた名前)、履歴内の要約、最初の発言の順に使う。

## テスト

```bash
python3 -m unittest discover tests
```

合成データで画面を試す場合(合成データは実際の作業記録ではない):

```bash
python3 tests/make_demo_data.py /tmp/cc-demo --live &   # 数秒ごとに記録を追記し続ける
python3 server.py --claude-dir /tmp/cc-demo --port 8766
```

## 構成

- `server.py` — HTTP サーバー(local / hub)、ログイン、SSE 配信
- `sync_client.py` — Mac から hub へ履歴の要約を送る
- `setup_autostart.py` — Claude Code 起動時の自動起動を登録・解除する(Mac / Windows)
- `autostart.py` — Claude Code の起動時に呼ばれ、画面サーバーと同期が止まっていれば起動する
- `sync_lock.py` — 同期を1つだけ動かすためのロック
- `static/manifest.webmanifest`・`static/sw.js`・`static/icons/` — アプリとして追加するためのファイル(ログイン不要で配信。履歴は含まない)
- `Dockerfile` — hub をコンテナで動かす
- `history_parser.py` — jsonl の差分読み込みとセッション集計
- `static/` — 画面(HTML/CSS/JavaScript、外部ライブラリなし)
- `tests/` — 単体テストと合成データ生成
