# Blender–Roblox Primitive and Mesh Sync

Blenderで作ったプリミティブモデルをRoblox Studioの標準Partとして再構築し、一般メッシュは同一形状ごとに1つのMesh IDを共有して配置するためのアドオンとStudioプラグインです。

Primitive Syncに加えて、共有Mesh ID、双方向Mesh Sync、Roblox MaterialのBlenderプレビューを含みます。設計指針は [SPEC.md](SPEC.md) を参照してください。

## ダウンロードと簡単インストール

[GitHub Releases](https://github.com/tappy3d-hue/blender-roblox-mesh-sync/releases/latest)から、次の2ファイルをダウンロードします。ソースコードをZIPにする必要はありません。

- `RobloxPrimitiveSync-Blender-0.10.7.zip` — Blender 4.2以降用Extension
- `RobloxPrimitiveSync-Studio-0.10.7.rbxm` — Roblox Studio用ローカルプラグイン

### Blender

1. `編集 > プリファレンス > エクステンション`を開きます。
2. 右上のメニューから`ディスクからインストール`を選びます。
3. ダウンロードしたBlender用ZIPを、展開せずに選択します。
4. `Roblox Primitive and Mesh Sync`を有効にします。

### Roblox Studio

1. ダウンロードした`.rbxm`をStudioの画面へドラッグ＆ドロップします。
2. Explorerに追加された`RobloxPrimitiveSync`を右クリックします。
3. `Save as Local Plugin`を選択します。
4. Studioを再起動するか、プラグインを再読み込みします。

更新時は、同じ固定名のローカルプラグインを置き換えてください。古いバージョンを同時に残すとツールバーが重複します。

## チーム開発

このリポジトリにはBlenderアドオン、Rojo用Studioプラグイン、テスト、仕様書を含みます。`dist/`、`.rbxm`、`.zip`、Pythonキャッシュは生成物としてGit管理しません。

Studioプラグインを更新する場合はRojoをPATHへ追加し、`Update Studio Plugin.cmd`を実行します。継続ビルドには`Watch Studio Plugin.cmd`を使用します。スクリプトは個人PC固有のパスを使用しません。

配布用ファイルは`Build Release.ps1`で生成します。Blender ZIPは`blender_manifest.toml`がアーカイブ直下にあることを自動検証し、Studio RBXMも同じバージョン名で`dist/`へ出力します。ソースフォルダー自体をZIPにするとmanifestが1階層下へ入るため、Releaseには使用しないでください。

ソースコードはMIT Licenseです。同梱マテリアルプレビュー画像については、公開配布前に [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を確認してください。

## フォルダー

- `blender_extension/` — Blenderアドオン
- `roblox_plugin/` — Roblox Studioプラグイン
- `examples/` — 共通JSONのサンプル
- `tests/` — Blenderを起動せず実行できる基礎テスト

## Blenderアドオン

1. `blender_extension`フォルダーをZIPにするか、BlenderのExtension開発用フォルダーへ配置します。
2. Blenderでアドオンを有効にします。
3. 3Dビューで `Shift + A > Roblox Parts` を開きます。
4. 形状を追加し、Nパネルの `Roblox` タブで設定します。
5. `Validate Scene`で検証してから`Export JSON`を実行します。

Object Modeでの移動、回転、拡縮を前提としています。Edit Modeで頂点を変更したオブジェクトは検証エラーになります。

### Tube近似

`Shift + A > Roblox Parts > Tube`から直線状の中空Tubeを追加できます。選択中の`Tube Approximation`で内径比率と3～64の分割数を変更します。Blenderでは1オブジェクトですが、`Export Roblox Primitive JSON`時に1セグメントあたり4個の標準WedgePartへ正確に分解され、StudioではTube名のModelへまとめられます。

`Send Selected to Studio`は標準PartとMeshPartを同時に送信できます。`Shift + A > Roblox Parts`で作成したもの、変換済みのプリミティブ、StudioからPartとして受信したものは標準Partとして再構築され、それ以外の有効MeshはMeshPartになります。Tubeは複数のWedgePartへ展開されます。

既定の16分割では64 Partsです。Primitive Syncパネルと選択Tube設定に展開後のPart数を表示し、Tube単体で128 Partsを超える場合と、シーン全体で500 Partsを超える場合は警告します。クリーンな直線Tubeメッシュは`Convert Existing Meshes > Auto Detect`または`Tube`指定で変換できます。Bevel、曲がったパイプ、途中に追加ループがあるMeshは初版の自動判定対象外です。

### 既存メッシュの変換

1. 1つのRoblox Partに対応するMeshオブジェクトを選択します。複数選択も可能です。
2. Nパネルの`Roblox > Convert Existing Meshes`を開きます。
3. 通常は`Auto Detect`を選びます。必要なら形状を手動指定します。
4. `Convert Selected Meshes`を実行します。

変換はBlock、Ball、Cylinder、Wedge、Corner Wedgeと、クリーンな直線Tubeを判定します。頂点だけでなく辺の中点と面の中心も検査するため、三角形化されたメッシュや分割数の異なる球・円柱も許容誤差内で判定できます。既定では元メッシュが非表示の`Roblox Conversion Backup` Collectionへ保存されます。

1つのMeshに複数の独立形状が結合されている場合は、Blenderの`Separate > By Loose Parts`で分割してから変換してください。Bevel、穴、Boolean形状、曲がった円柱など、標準Partと一致しない形状は変換しません。

回転を適用済みの直方体も、面法線からローカル軸を復元して判定します。結合・分離によって古いプリミティブ情報が引き継がれた場合は、そのオブジェクトを選択して`Convert Selected Meshes`をもう一度実行すると、形状を再解析して署名とGUIDを作り直します。

## Studioプラグイン

### 開発中の自動更新

Studioの`File > Studio Settings > Studio`で`Plugin Debugging Enabled`と`Reload plugins on file changed`を有効にします。以後は`Update Studio Plugin.cmd`をダブルクリックすると、現在PluginDebugServiceが参照している固定ファイルへ直接ビルドされます。継続開発時は`Watch Studio Plugin.cmd`を起動したままにすると、Luauファイルの保存ごとにRojoが再ビルドし、Studioが自動再読み込みします。ファイル名に旧バージョン番号が残っていても、実際の実行バージョンはウィジェットタイトルで確認します。

自動再読み込みが動かない場合は、Explorerの`PluginDebugService`内にある同プラグインを右クリックして`Reload Plugin`を実行します。外部ビルド後は、古い内容を書き戻す可能性がある`Save and Reload Plugin`ではなく`Reload Plugin`を使用してください。

プロジェクトのルートフォルダーへ移動してから`roblox_plugin/default.project.json`をRojoでビルドし、生成したモデルをStudioでローカルプラグインとして保存します。

```powershell
cd "C:\path\to\plugin-development"
rojo build .\roblox_plugin\default.project.json --output RobloxPrimitiveSync.rbxm
```

プラグインの`Import JSON`からBlenderが出力した`.json`ファイルを選択すると、Workspaceへ標準PartのModelを生成します。

## Mesh Instancing Sync

同じメッシュを個別にFBXインポートせず、代表メッシュを1回だけRobloxへ登録して全インスタンスでMesh IDを共有します。Material Color Gridは必須ではありません。UV、アトラス、PBR画像、頂点カラーは送信前に完成しているものをそのまま使います。

### 初回設定

1. Rojoで生成した`.rbxm`をStudioへ読み込み、`Save as Local Plugin`でローカルプラグインにします。
2. BlenderからStudioへMeshPart／PBR画像を新規登録する場合は、Studioの`File > Beta Features`で`CreateAssetAsync Lua API`を有効にしてStudioを再起動します。この項目は標準Partだけの同期やStudioからBlenderへの送信には不要です。項目自体が表示されないStudioでは、同APIが正式提供済みかStudioが古い可能性があるため、まずStudioを最新版へ更新してください。
3. 初回だけBlenderの`Nパネル > Roblox > Mesh Sync`で`Allow Studio Connection`を押します。ローカルペアリングが60秒間、1回だけ許可されます。
4. Studioの`Blender Mesh Sync`でBlenderに表示されているポート番号を確認し、Studioの`Port`欄を同じ番号にして`Connect`を押します。トークンや接続コードのコピーは不要です。入力したポートと自動取得したトークンは保存され、次回以降は自動接続します。接続を破棄する場合は`Forget Connection`を使用します。

接続確認は入力ポートを1秒で確認し、応答がない場合だけ既定ポート`27182`を1回確認します。通常のメッシュ転送とは別の短いタイムアウトを使うため、古い接続情報が残っていてもConnectが約30秒停止することはありません。

Experience Settingsの`Allow HTTP Requests`を有効にする必要はありません。初回接続時にStudioがこのローカルプラグインへ`127.0.0.1`との通信許可を求めた場合だけ、そのプラグイン権限を許可してください。`Allow Mesh / Image APIs`も事前に有効化せず、所有権のあるMesh／画像を読み取れない場合に限り、表示されたエラーとAsset権限を確認します。

`Asset upload failed: CreateAssetAsync ... not available yet`と表示された場合は、`CreateAssetAsync Lua API`が無効です。有効化後はStudioの再起動が必要です。[AssetService:CreateAssetAsyncの公式仕様](https://create.roblox.com/docs/reference/engine/classes/AssetService#CreateAssetAsync)

Studioローカルプラグインは常に`%LOCALAPPDATA%\Roblox\Plugins\RobloxPrimitiveSync-Studio.rbxm`へ出力します。更新時はこの固定名ファイルを上書きし、バージョン番号付きや`-Dev`付きのコピーをPluginsフォルダへ追加しません。`Update Studio Plugin.cmd`と`Watch Studio Plugin.cmd`もこの正確な保存先を使用します。

### スキーマ不一致エラー

StudioのOutputに古いバージョン名と`Mesh Sync schema or revision mismatch`が表示された場合は、重複した旧Studioプラグインが動作しています。旧ファイルの拡張子を`.disabled`へ変更し、`Update Studio Plugin.cmd`で固定名の開発版を更新してから再接続してください。新しいウィジェットのタイトルには現在のバージョンが表示されます。

### 送信

1. Blenderで送信する静的Meshを選択します。
   - 形状を常に連動させたい複製は、基準にするオブジェクトを最後に選び、`Link Mesh Data to Active`を押します。これは`Ctrl+L > Link Object Data`と同じ処理で、形状だけでなくUV、頂点カラー、マテリアルも共有します。
2. `Selected Mesh Settings`で外観を設定します。
   - `Auto` — PBR画像、既存頂点カラー、Roblox Materialの順で自動選択
   - `Texture / PBR` — Base Color、Roughness、Metallic、Normalの接続画像を使用
   - `Vertex Color` — アクティブな既存Color Attributeを使用
   - `Roblox Material` — Roblox標準Materialと単色を使用
3. 複数選択へ同じ設定を使う場合は`Apply Active Settings to Selected`を押します。
4. 必要に応じて接続欄の`Position`、`Rotation`、`Scale`をオフにします。オフにした項目は既存Studioオブジェクトの値を維持します（新規オブジェクトには初期値を送ります）。
5. `Send Selected to Studio`を押します。PartとMeshPartを混在して送信でき、接続後に作成された新しいrevisionだけをStudioが自動受信します。

インポート完了後は、同期ルートFolderではなく、そのrevisionで追加または更新されたPart／MeshPartだけがStudioで選択されます。

Blenderでの編集そのものは自動送信せず、`Send Selected to Studio`を押した後の受信だけを自動化します。Studioは接続時点ですでにBlenderサーバーに残っていたrevisionを基準値として無視するため、新しいプレースを開いて接続しても前回送信分を勝手にインポートしません。接続後に作成されたrevisionだけを自動反映します。`Sync Current Revision`は、現在サーバーに残っているrevisionを意図的に再受信する場合や通信失敗時の再試行に使用します。

同じ頂点、面、法線、UV、頂点カラーを持つメッシュは同じMesh IDになります。位置、回転、正のObject ScaleはID判定に含まれません。内容が未変更なら、次回送信でもローカルプラグインのキャッシュから以前のAsset IDを再利用します。

同じMeshデータに同一設定のBevelモディファイアがある場合は、Blenderが生成UVをオブジェクトごとに揺らすことを防ぐため、代表オブジェクトの評価結果を共有します。Bevel設定が異なる場合や、オブジェクト依存のモディファイアは個別に評価されます。

### Material Preview

`Selected Mesh Settings > Use Roblox Material`を有効にすると、その下でWoodやBrickなどのRoblox標準MaterialとColorを選択できます。この場合だけ標準MaterialとしてStudioへ送り、Blender Materialのノードや名前から標準Materialを自動判定しません。オフの場合は`Auto`、`Texture / PBR`、`Vertex Color`、`None`からBlender側の外観を選びます。

`Live Material Preview`を有効にすると、Roblox Material、Color、Transparencyの変更がBlender上へ即時反映されます。Roblox標準MaterialはUVではなく、8 studs周期のワールド座標ベースBox投影で近似します。BlenderのTexture / PBRは元のMesh UVを使用します。PlasticとSmoothPlasticは画像テクスチャを使用せず、Roughnessをそれぞれ0.8と0.25に固定します。BlenderのWorldとライトは変更しません。

ColorはBlender内部のシーンリニアRGBとRobloxのsRGBを送受信時に変換します。Blenderのカラーピッカーへ入力したHEX値は、StudioのColorでも同じHEX値になります。

プレビュー対象は同梱ライブラリに存在する43種類だけです。`Air`と`Water`、旧プロシージャル近似は含みません。プレビューはObject単位で割り当てられるため、同じMesh Dataを共有するオブジェクトでも異なる外観を確認できます。`Refresh Material Preview`で選択中の表示を再構築できます。`Use Roblox Material`をオフにすると元のBlender Materialへ戻ります。

### StudioからBlenderへ送信

1. BlenderでMesh Sync Serverを起動し、Studioプラグインを接続します。
2. StudioでPart、MeshPart、Model、Folderを選択します。
3. 必要に応じてStudioプラグインの`Position`、`Rotation`、`Scale`をオフにします。既存Blenderオブジェクトではオフにした値を維持します。
4. `Send Selection to Blender`を押します。ModelまたはFolderを1つ選ぶだけで、配下の全子孫にあるPart／MeshPartが再帰的に収集されます。途中にConfiguration、Accessory、BasePartなどが挟まっていても探索を継続します。既定の`Auto Apply from Studio`では直ちにBlenderへ適用され、適用全体はBlenderのUndo対象です。

確認してから反映したい場合だけ`Auto Apply from Studio`をオフにし、`Review Incoming`と`Apply Studio Selection`を使用します。自動適用中に同じGUIDのローカル変更が見つかった場合は、明示的にStudioから送った内容を採用します。

PartはBlenderプリミティブへ、Studio由来の新規MeshPartはEditableMeshから静的Meshへ復元されます。Blender由来で同じGUIDを持つ既存MeshPartは、既定の`Preserve Blender Geometry`により元のMesh Data、四角面／N-gon、モディファイア、Linked Mesh Data、原点を維持し、Transform・外観・物理設定だけを更新します。StudioでMeshIdが変更されていた場合も形状を上書きせず警告します。

新規または形状置換時の`Mesh Topology`は、正確な`Exact Triangles`、属性境界を保護しながら推測結合する`Join to Quads`、同一平面を推測結合する`Dissolve Coplanar`から選べます。`Merge by Distance`は既定オフです。SurfaceAppearanceとMaterialVariantの取得可能なPBR画像は内容ハッシュで共有され、`.blend`へPackされます。Blender由来MeshPartの形状を読み取れない場合はプロパティだけを更新し、Studio由来の読み取れないMeshPartだけをスキップします。

複数選択またはModel／Folderの再帰送信では、読み取り権限がないMeshPartと、公開APIから形状を取得できないUnionOperationなどの未対応BasePartをスキップし、標準Partと読み取り可能なMeshPartは送信を続行します。スキップした個数、オブジェクトパス、取得できる場合はAsset IDをStudioのステータスとOutputへ表示します。同じ読み取り不可Assetを共有するMeshPartは権限確認と警告を1回にまとめます。選択対象がスキップ対象だけだった場合は、空の同期を作らず送信を中止します。

SurfaceAppearance、MaterialVariant、MeshPart Textureの画像がAsset権限により読み取れない場合は、画像だけをスキップし、形状、Transform、Material、Color、Transparency、物理設定をBlenderへ送ります。Blender上では既存Materialを変更せず、権限不足の警告をObjectへ保存します。そのObjectをStudioへ戻した場合は、既存のSurfaceAppearance、MaterialVariant、TextureIDを引き継ぎ、読み取れなかった外観を単色で上書きしません。

StudioのCFrameはワールド座標として受信し、BlenderでEmptyを親に設定する際もワールド位置・回転を保持します。ModelとFolderが交互に入れ子になった階層は、Blenderで表現できない交差部分をメタデータとして保持し、Studioへ戻すと元の親子関係を復元します。

### 階層

BlenderからStudioへの同期ルート名は保存済み`.blend`のファイル名で、Workspace直下のFolderとして作成されます。Blender Collectionはその中のFolderへ変換され、Collectionに属さないオブジェクトは同期ルート直下へ配置されます。独自の`Scene` Modelは作成しません。Emptyはシーン既定またはEmptyごとの上書きでModel／Folder／Ignoreを選択できます。CollectionやEmptyの意味とGUIDはAttributeへ保持されるため、Studio上の表示クラスとBlender上の階層を分離できます。今回同期したCollectionに対応する旧Modelだけは、子を維持したままFolderへ移行します。

往復同期では、選択物の祖先にある`BlenderModelId`と同期ルート種別を再利用します。`Preserve Blender Hierarchy`が有効な既存オブジェクトでは、全Collection所属、複数Collection所属、親Empty、Collection／Emptyの親子関係をBlender側のまま維持します。新規オブジェクトまたは同設定が無効な場合だけStudioの階層を反映します。同期ルート自体は子Hierarchyとして再送信しないため、`Studio Selection.001`のようなルートが往復ごとに増えることはありません。異なる同期ファイルのオブジェクト、または同期済み／未所属オブジェクトを混ぜた送信は停止します。

Studio由来ドキュメントの同期IDは各Blender Objectへ保存し、受け皿専用の`Studio Selection` Collectionは作成しません。旧方式の同名Collectionが現在の同期対象に対応する場合は、子Collection、Empty、オブジェクトをシーンへ安全に移してから受け皿だけを削除します。

Studioで最初にグループ化したModelをBlenderへ送った場合、そのModelと子Partは同期Folderの外側にあってもGUIDで再発見されます。Studio由来のModel自体を同期境界として再利用し、Modelが別のModel／Folder／Partの下にある場合も現在の親を変更しません。Blenderから送り直す際は既存Model内で子Partを更新するため、Modelが元の階層から外れたり、同じModelが複製されたりしません。

Blender由来の平坦なオブジェクトをStudio側だけでModel／Folderへまとめた場合、`Preserve Blender Hierarchy`によりBlender Outlinerを平坦なまま維持できます。Blenderから再送信されたデータに親指定がなくても、Studioで作成した既存Model／Folderを優先してPartを同じグループ内で更新します。

### 制約

- Blender 4.2以降の静的Meshのみ対応します。Armature、Shape Key、負スケール、ミラー変形、シアーは対象外です。
- Texture/PBRは1オブジェクトにつき、アトラス化済みの1マテリアルを前提とします。
- 画像は最大1024×1024、メッシュは最大20,000三角形です。
- Normal MapはRobloxが要求するOpenGLタンジェント空間形式を使用してください。
- 選択送信はGUIDが一致するStudio上の生成MeshPartだけを更新し、未選択の生成物と`Studio Only`は削除しません。
- 資産はStudioへログイン中のユーザーに作成されます。形状や画像の内容が変わると、新しいAsset IDを作成します。
- Mesh／Image Assetの登録とインスタンスのステージングが完了してから、Workspace・MaterialServiceを1つのUndo記録内で更新します。更新中に失敗した場合は記録をCancelして変更を巻き戻します。Asset登録そのものはStudioのUndo対象外です。
