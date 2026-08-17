# Blender–Roblox Primitive Sync 仕様書

バージョン: 0.4  
ステータス: 実装済み指針

## 1. 目的

Blenderで制作したプリミティブをRoblox標準Partとして再構築し、一般Meshは同一形状ごとにAsset IDを共有する。Primitive SyncはBlenderを正本とし、Mesh Syncは選択範囲を双方向同期する。

## 2. システム構成

- Blender アドオン
  - Roblox 対応形状の作成
  - GUID と Roblox 用プロパティの管理
  - モデルの検証
  - 共通 JSON 形式への出力
  - ローカル同期サーバーとStudio受信キュー
- Roblox Studio プラグイン
  - JSON の読み込み
  - 標準 Part による再構築
  - Meshの差分・双方向同期
  - Undo / Redo 対応

Primitive Syncは`Blender -> Roblox Studio`、Mesh Syncは`Blender <-> Roblox Studio`とする。

## 3. 正式対応形状

| Blender 側 | Roblox 側 |
| --- | --- |
| Block | `Part` / `Block` |
| Ball | `Part` / `Ball` |
| Cylinder | `Part` / `Cylinder` |
| Wedge | `Part` / `Wedge` |
| Corner Wedge | `Part` / `CornerWedge` |
| Tube | 1セグメントにつき4個の`WedgePart` |

中空 Tube は標準 Part に存在しないため、Blenderでは1オブジェクトとして扱い、書き出し時に各セグメントを4個のWedgePartへ展開する。分割数と推定 Part 数を事前に表示する。

## 4. Blender の操作

`Shift + A > Roblox Parts` に標準5形状とTubeを登録する。同じ追加処理を3DビューのNパネルからも利用できるようにする。追加位置は3Dカーソルとする。

各対応オブジェクトには次の情報を保持する。

- 永続 GUID
- Roblox Part 種別
- Roblox Material / Color
- Transparency
- Anchored
- CanCollide / CanTouch / CanQuery
- CastShadow
- 同期対象フラグ
- 作成時メッシュ署名

位置、回転、Object Mode のスケール変更は許可する。頂点編集、対応外 Modifier、負またはゼロのスケール、GUID 重複、Roblox サイズ範囲外は検証対象とする。対応外オブジェクトはシーン内に共存できるが同期しない。

## 5. 座標とデータ形式

基本軸変換は次の通りとする。

```text
Blender X -> Roblox X
Blender Y -> Roblox -Z
Blender Z -> Roblox Y
```

回転は Euler 角ではなく3x3回転行列として転送する。JSON の `cframe` は位置3値と回転行列9値の合計12値とする。単位変換係数は設定可能にし、既定値は `1 Blender unit = 1 stud` とする。

スキーマ識別子は `roblox-primitives/1` とする。ライブ同期とファイル同期は同じスキーマを使用する。

## 6. 将来のローカル同期

Blender が `127.0.0.1` のみでローカルサーバーを起動する。既定ポートは `27182` とし変更可能にする。

想定 API:

```text
GET /v1/health
GET /v1/revision
GET /v1/model
GET /v1/changes?since={revision}
```

Studio はリビジョンを定期確認し、変更時だけ全体または差分を取得する。差分種別は `create`、`update`、`delete` とする。GUID で Blender オブジェクトと Studio Part を対応させる。

サーバーはセッショントークンを要求し、任意 Python / Luau コードの実行機能を持たせない。

## 7. Studio 側の所有範囲

同期ファイルごとに専用Folderを生成し、自動変更の対象をその配下に限定する。Blender CollectionはFolderへ変換してExplorerだけを整理し、ビューポートでCollection全体がまとめて選択されないようにする。Emptyだけを設定に応じてModel、Folder、または非表示として扱う。

```text
Workspace
└─ BlendFileName (Folder)
   ├─ CollectionName (Folder)
   │  ├─ EmptyName (Model / Folder / Ignore)
   │  └─ Parts / MeshParts
   └─ Collection未所属のParts / MeshParts
```

生成 Part には少なくとも以下の Attribute を設定する。

- `BlenderObjectId`
- `BlenderModelId`
- `BlenderRevision`
- `BlenderPartType`
- `GeneratedByBlenderSync`
- `BlenderRootKind`
- `BlenderHierarchyKind`
- `BlenderHierarchyId`
- `BlenderOriginalParentId`
- `BlenderCollectionIds`

## 8. 対応プロパティ

初期対象:

- Name / Shape / Size / CFrame
- Color / Material / Transparency
- Anchored / CanCollide / CanTouch / CanQuery / CastShadow

初期対象外:

- Texture / UV / SurfaceAppearance / PBR
- Animation / Bone / Motor6D / Constraint / Script
- Studio から Blender への双方向編集
- MeshPart への自動フォールバック

## 9. 実装フェーズ

### フェーズ1: 基本 MVP

- Blender アドオンの基本構造
- `Shift + A` とNパネルから標準5形状を追加（後続フェーズでTubeを追加済み）
- GUID とプロパティ管理
- 検証
- 既存Meshの自動判定・手動指定変換
- JSON 出力
- Studio でJSONを読み、標準 Part として再構築

### フェーズ2: ローカル同期

- Blender ローカルサーバー
- Studio から接続
- Sync Now / 全体同期
- Undo / Redo

### フェーズ3: 安全な手動差分同期

- リビジョン管理
- GUID による追加・更新・削除
- 手動送信のみとし、編集中の自動送信は行わない
- 接続切断からの再接続
- Workspace更新全体のUndoと、失敗時の自動ロールバック

### フェーズ4: 制作支援

- Tube 近似（直線・3～64分割・WedgePart展開を実装済み）
- Part 数予測と最適化警告
- Material UI と検証表示の強化
- Collection と Model 階層の対応

## 10. フェーズ1完了条件

1. `Shift + A` から標準5形状を追加できる。
2. Blender で配置、回転、Object Mode の拡縮ができる。
3. 不正形状や設定を検出できる。
4. 共通 JSON を出力できる。
5. Studio でJSONを読み、標準 Part を生成できる。
6. 座標、回転、サイズ、基本プロパティが再現される。
7. Studio の Undo でインポートを取り消せる。
8. 同期対象外のオブジェクトを変更しない。

## 11. Mesh Instancing Sync

標準Partへの変換とは別に、一般の静的Meshを同一形状ごとに1つのRoblox Mesh Assetとして登録し、複数のMeshPartで共有する。

- スキーマは`roblox-mesh-sync/1`とし、Primitive用の`roblox-primitives/1`とは分離する。
- Blenderは選択Meshだけを`127.0.0.1:27182`からStudioへ送る。
- 頂点、三角形、分割法線、UV、既存頂点カラーをメッシュ内容の署名とする。位置、回転、正のObject Scaleはインスタンス情報とする。
- 未変更のメッシュと画像は内容ハッシュから以前のAsset IDを再利用し、変更時は過去のAssetを更新せず新しいIDを作る。
- Base Colorのみの場合は`MeshPart.TextureContent`、PBRの場合は`SurfaceAppearance`、標準Materialの場合は`BasePart.Material`を使用する。
- Base Color、Roughness、Metallic、OpenGLタンジェント空間Normal、既存Color Attributeへ対応する。ベイクやアトラス生成は行わない。
- 選択オブジェクトはGUIDによって追加・更新し、未選択オブジェクトは削除しない。
- Armature、Shape Key、負スケール、シアー、1024pxを超える画像、20,000三角形を超えるMeshは検証エラーとする。

## 12. 双方向同期とMaterial Preview

- BlenderからStudioはPartとMeshPartを混在できる`roblox-mesh-sync/4`、StudioからBlenderは`roblox-mesh-sync-reverse/3`を使用する。Studioは従来の`roblox-mesh-sync/1`～`/3`、Blenderは従来の`roblox-mesh-sync-reverse/1`～`/2`も受信する。
- `Send Selected to Studio`は、Roblox Partとして登録済みのBlenderオブジェクトを標準Part、それ以外の有効MeshをMeshPartとして同じリビジョンで送る。位置・回転・スケールは個別に更新を無効化でき、新規作成時は初期Transformを必ず設定する。
- `.blend`名のFolderを同期ルートにし、CollectionはFolderとして転送する。Collection未所属オブジェクトはルート直下へ配置し、`Scene` Modelや固定の`Generated Meshes`フォルダーは作成しない。Emptyはシーン既定と個別上書きによりModel／Folder／Ignoreを選択でき、元のHierarchy種別とGUIDはAttributeへ保持する。
- Studioの選択Model／Folderを再帰的に送信し、Partは対応プリミティブ、MeshPartはEditableMeshとしてBlenderへ復元する。
- Blender由来で同じGUIDの既存MeshPartは、`Preserve Blender Geometry`が有効ならMesh Data、四角面／N-gon、モディファイア、Linked Mesh Data、原点を置換せず、Transform・外観・物理設定だけを更新する。読み取り不能なBlender由来MeshPartもプロパティ更新を許可し、MeshId変更時は形状を保持して警告する。
- Studio由来MeshPartの復元は`Exact Triangles`、`Join to Quads`、`Dissolve Coplanar`を選択でき、任意の`Merge by Distance`をトポロジー処理前に適用する。推測結合ではUV、法線、色、シャープ境界を保護する。
- 往復時は選択物の祖先にある同期ルートGUIDと`BLENDER_SCENE`／`STUDIO_SELECTION`種別を再利用し、同期ルート自身を子Hierarchyへ含めない。`Preserve Blender Hierarchy`が有効な既存GUIDでは、全Collection所属、親Empty、Collection／Empty階層をBlender側のまま維持する。異なる同期ルートを混ぜた送信は停止し、`Studio Selection`ルートの再帰的な増殖を防ぐ。
- 受信データはBlenderでReview後にApplyまたはDiscardする。ローカル変更済みオブジェクトはGUID単位で競合を表示する。
- `Use Roblox Material`が有効な場合だけRoblox Material、Color、Transparencyを送信し、Object単位のライブプレビューを生成する。無効時はBlender Material／PBR／頂点カラーを使用し、Linked Mesh Dataを変更しない。
- 標準Materialプレビューは同梱するStudio OBJ書き出し由来の43種類のMTL・diffuse・normal・specular画像だけで構築する。画像はTexture CoordinateのUVを使ったFlat投影とし、PlasticとSmoothPlasticではNormal Mapを使用しない。ライブラリにない`Air`と`Water`および旧プロシージャル近似は提供しない。
- PBR画像は受信時にBlender Imageへ復元して`.blend`へPackする。BlenderのWorld、ライト、カラーマネジメントは変更しない。
- 初回接続はコード入力を使用しない。Blenderの`Allow Studio Connection`で60秒間のローカル・単回ペアリングを許可し、Studioが`127.0.0.1:27182`から接続トークンを自動取得して保存する。ペアリング用エンドポイントは許可時間外にはトークンを返さず、通常の同期APIは引き続きトークンを必須とする。
