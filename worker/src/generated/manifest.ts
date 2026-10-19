// 自治体 → R2 のパックの鍵 (内容のハッシュつき)・状態の KV の鍵
// nationwide/packs/build_packs.py が作る。手で直さない
export default {
 "format": "gomisute-manifest/1",
 "builder": "nationwide/packs/build_packs.py",
 "municipalities": {
  "172014": {
   "name": "石川県金沢市",
   "short": "金沢市",
   "engine": "district_table",
   "data_version": "kanazawa-fy2026-0aff027aa6c8",
   "status_key": "status:172014",
   "bbox": [
    136.5454,
    36.3331,
    136.8222,
    36.6789
   ],
   "packs": {
    "core": {
     "key": "172014/core.e55b15be0903.json.gz",
     "sha256": "e55b15be09031c2f60d0d46be43c60c1a1427d665b51b1991638564129244c74",
     "bytes": 141268,
     "gzip_bytes": 9679
    },
    "addr": {
     "key": "172014/addr.dffba5e09837.json.gz",
     "sha256": "dffba5e0983790e913c383018079abe39ea78176ad6ec357d990d1c847927c36",
     "bytes": 1349385,
     "gzip_bytes": 145930
    },
    "loc": {
     "key": "172014/loc.fe136e5e4519.bin.gz",
     "sha256": "fe136e5e45198aee550017c86fdd1a92aff399f85364f4395c45bbd93e20ac6f",
     "bytes": 2619856,
     "gzip_bytes": 1912618
    }
   },
   "tiles": {
    "loc": {
     "prefix": "172014/loc/",
     "size_m": 2000.0,
     "halo_m": 450.0,
     "count": 151,
     "bytes": 18981456,
     "gzip_bytes": 6868835,
     "files": {
      "-28_31": "99e01663170b4aa223231238c485ca3f89200d8d0cdcf8b784ef48648d649d49",
      "-28_32": "4d0345f053f6f4fafde84f8488b350d2dd574b90e4b56961eae6b9ea3ae2ba7f",
      "-27_30": "97a4ef5469317f774981aa019d7ff5523a9d66e104937ed52028c3a51995f3d1",
      "-27_31": "40383d585d6034c0ef1ff2c39c506b8a62342bde286b8be49426921e624e8dc0",
      "-27_32": "fffd6fed16815a89f29eaf58933965f357a41e78eb4aa85ebd840fbef68f4801",
      "-27_33": "1752d0efcfb7e0ea542404e028dce7e18fdc650c17b70575e62b8f63f1a90db6",
      "-27_34": "7811f991e0ce7ac19e3588d977bf3e1e91ec8e10763b6077e57f6b72f66e570e",
      "-26_30": "4a198017489e8b02e075e804344586a4e05d747e97cff18722af19ed859ba25b",
      "-26_31": "a521331dd87bd581658332c6928af1459fe9df2801e145c5cc188955cbd64b60",
      "-26_32": "c9be8b7c16b7eb3df67bb99a4b14a6f5f74a5f468b6d62819b756b070fab98f9",
      "-26_33": "db0cb9cac618e2804b9e25581c1b5e0272f8c25695ba29f3c13c09e4f0c2c86a",
      "-26_34": "19637811d8597199dc924f739bfb5bbdfdf5ae110b08724133e51f419f66a201",
      "-26_35": "d168e66cb12be6478131706702b5c6d1d1fa728a9cbb6cb6b5a501c466427b64",
      "-25_27": "3e89f823bf9335bf8d012918443e1f0c34a80961bbded780b6b69fdd8c594451",
      "-25_28": "3b3d39f60047516edf047e570531151ec750e6a11979c059f92294916db71598",
      "-25_29": "8df0b03775c7f978baeb16e2efe6bdcf720a6ec7a6d24e873d9d9928d50b336e",
      "-25_30": "54599fb04a39b539bc320ccf372428dafe469aae2c4ee4eb5d29668435148abc",
      "-25_31": "291f561495f039fac42ab78f7307b041df8fc6fb794a91f309faa45ac5b570b9",
      "-25_32": "b96a60a444aa78d5e81b2694ec8d38ece310845f9bd04c34b683ca8ee34a5d15",
      "-25_33": "3a5225a7ef19b15a3f183cb643d85416bd50c108226d92fcb071e73b5c1f88ad",
      "-25_34": "d6c2f58ab746fad7fa40805ab0592ffbf95704165906184e9be6611109723ae0",
      "-25_35": "716a5a430646995a5ee4055416cfb84e2ce880cd5e48fec44e82cac450ff4d3f",
      "-24_24": "e3619b7bd552e972f0bf5821fd873456d37e2fcbedde199dec8a7c5feea7dd11",
      "-24_25": "641b2b7eddd172586b8454fe6e9a80ec9f5dd099667add17aabd6e11e1b3ba24",
      "-24_26": "3060a3f6c71cf07e41e7492ddfccc3d3bd225664c31971d1ba6a9d6a609efe95",
      "-24_27": "657e198942546fbec63995de28c11ee77b5cc5df1551aa2e9053878865c97954",
      "-24_28": "f8ebbde692f41144c40c28df8cf4643b970b42c62baafc3a49c24ff9a48b89e6",
      "-24_29": "edfd7907433d526ad792a97587c189ea568c8feaa0edede2b5fac78f173b4db7",
      "-24_30": "6defcfa6221e31f0b23d2713bcde8a2166811a7e649def3e9566033a164c5ef2",
      "-24_31": "5bfd0f543201132d5b82b00b60900cb0b7e4dcef727d762fac6aed32258b20cd",
      "-24_32": "24ea79eab2e986f1929f31cc9108ba1f0e981230a29a8649244e2d30f3ee5126",
      "-24_33": "0e103b1b7c04cb81d95b3fe3e1ca5ae301ff27a7c2c7110e2ccad5d11471aeb1",
      "-24_34": "6c41429b08698e79e9c52d9a2a4c8098881d10919aad027296957cf5f53da8d4",
      "-24_35": "bd926da3e5cb38128e81c80683e27cc154c8990c1632d5cbddf2c6c220457ae7",
      "-23_22": "95b28d32716bf80d8c7cd67b8ffb42fe284c4099fff321a0ce27872a94c3ba3e",
      "-23_23": "b77cfad11be33cf105cfd9e02bf2243295de50d3dff706363091f2e6b695a85f",
      "-23_24": "b380d9d8fd9209d9786515ea808c4a8d3ccb64d515f26195212ffffd4e112d1f",
      "-23_25": "bfb69566944f94a1d7c4c5877cf5d7f95d7b44e1e1809b85cd1bb911476d9c85",
      "-23_26": "61be64bdba38cfd40fd1c2ffc9440441d07c2e4f35446b4904fca5057f5f0773",
      "-23_27": "e3adf1c0d98ab1fe299c8c84f3b8b783e44925b9dcf69f1055fcf2d7a66a976d",
      "-23_28": "95bdd1dd608f94793fb00a7e6c468830372076e6b3e67a147f6249eb9c7cb03f",
      "-23_29": "120c2f128becd3d2185f28d2784e24b4d55ffd2cdcac2345ac3f0551fac315b7",
      "-23_30": "8955d4d528b1711bc426cdd5dfb6b1043dba20b23df0d40f421de25ba522199c",
      "-23_31": "cfb87d54af5d03d4af34bb2c5d8ba376c44b30ea8c2b9c448087c477190b2ac1",
      "-23_32": "8b31541101c9624ae6819ac019954d2da72a04b5d6670545eee673be94448f80",
      "-23_33": "3c110a1d6f2d0e8ebaa40578a9a84374cc641301daa9871223c79a363379524b",
      "-23_34": "cc36f02efccd003ff20ce367a70ee31255af68dc98cd5425ecba8644507a5387",
      "-23_35": "e44ea427dec3d8f924bbc9b2ba4115dc19d1b7e8ad12247c0cdecfdb64589833",
      "-23_36": "4433cb8813b44712cd54b5148846e16c55ce0cd95d7757215d188ebc2674a6ba",
      "-22_21": "bd8d1651cb98c0a373f8633673b14314ff782adcb8f3ce2d1a3a0b0e94fe357e",
      "-22_22": "1e02afaa02af470c26fae14bf7ef8ab71acfa19b2155bf0377e0e34f8a628942",
      "-22_23": "c1b075cad18569d17829fa606815f4e7ce35274d229e838e1f10b1e725537cb8",
      "-22_24": "6ddbd96215bd64ef9e63c93fe2730babde4f7a5664f052564913b83d10cf38ff",
      "-22_25": "4b0b2920bc9cc6223cf4b2833540dde924c32c96f47ec4bbf1a23f0830718aab",
      "-22_26": "06b13cfa99f4d397613cb2196a70ed32e6066e80610d2dc96b53c9a3d55229c5",
      "-22_27": "c8790e8267f232f2f3e2ce6da490e3549ed3eed9d600467e073f76f86dfe0f88",
      "-22_28": "a6d1c6d9418b328f0cd81ce9b696b216557d69f47c40c458ace543377ba00c82",
      "-22_29": "aa9bcae804f483c7f7993aec72c47929be2ecb83de2f85205d1c9645f24f0c43",
      "-22_30": "eec3bd5bb1d63ff21ce3e15bbe419d8b97f010df6f5a30d2e928605a0cc910fa",
      "-22_31": "33749cdcd4b747331a734f0ee0c82bb444b00d793910802e386bbde9b17f8b9f",
      "-22_32": "52415d1dddf8fb0386f1f79540b80c59d0e696385763dd7312aad722c1e8e801",
      "-22_33": "26454d4f5c484c597c709a8534ead0b20a1cf231baa97bba8b431d47a4664359",
      "-22_34": "8208c45db34f7c902cc53f87a104237ddd14df0f605664a0ac4669d19e0c34f4",
      "-22_35": "2f7abcb093104b910992ceabdb7b519013c28b5955a185c0d9e14393aef4eafb",
      "-22_36": "a9026189faf5f940a4297a661875138011d2502276daf49300de6837c07f9033",
      "-22_37": "2bc7bf6e8deae98581102463931828cb7544598701c496a411cbfe0b589e6d35",
      "-21_20": "9762eb1f2ffd625c72fed4ed7b59d8f6f2f65edcf3f92dbe9e4cbf4acf59561f",
      "-21_21": "ef33cbb325096820a8f35919e404c99c6b33c1efea14e044e47d4cb449421442",
      "-21_22": "7db79f23be47c32cbb4e2cc884a521ea1c72b8b141d554f4677e09c8d23cc4ea",
      "-21_23": "f6b39546ccf15004d70ebfdf3aef75c85753949960a82ab061950fc5d19a54d1",
      "-21_24": "ded1a1e506bcee9be2fd49f6c3025a5dd5464bf37f0e0eb80c6014117885f4b9",
      "-21_25": "3133533eaaeaeb4b2379a124afe75cc294c48cf43a42f64ae394bf46af18eb14",
      "-21_26": "9c0113316df77ccf33c430c39440cf6d009853b297d85dc9370c5c7ca4f69acf",
      "-21_27": "a0ee36b4e11803ca02ad44cfad51e078a2ce381b54ba8904a76af13349e49062",
      "-21_28": "9ade63baadb5965d83fae880ab021ce16fcbf00f9137b5aab6e5f35af17d518b",
      "-21_29": "790576f256ded39ec3aefb03e76961f7138c56bb8fff7021cdd62ec5db586e0e",
      "-21_30": "4a97cf6ebbeff65e77d08675d3b62c5bdb76f8f3d75f4ad809d87625e1099882",
      "-21_31": "0328c444d91411feae33bb69dc98935253121350384c84e05849a4caccd8c6cc",
      "-21_32": "dd0f386f00c47ea66402600511a887351a07e6101c97af451aebdd4506506ce1",
      "-21_33": "fe8f6192d177bd88030cef20c0f0983360efd6723e8a58ce629c7aa95cdcf8df",
      "-21_34": "0abec52607440d495bc13cd1aefbca310bf06086fd18e17661dfcda517f05522",
      "-21_35": "411b5b78daa387bb14f83448476b7497f7e0fd9ec989c3efebff4fb61dfc1002",
      "-21_36": "9ae6c8e4dfe5f5ea04578eda732fec2d15da89d4ab7e1bce9d5c31b898ed51a8",
      "-20_20": "8c87218b1ae879761b83b5cd71743b8c6d460e0c94d62f7b4e7a91425e52d11d",
      "-20_21": "20b9d71fd1e13d1e7801dba52bce77ee176ea027f28bf9c34838192758e2ec77",
      "-20_22": "d09610b5a963dbe201364ae0b723aeb18a0977db376f84b997d615329db22008",
      "-20_23": "507a46cb2fe31e82f768d67e7077aa5c0f98127737585d5d55999f45703c95d6",
      "-20_24": "a02876d8a934388c44b5999e7d58cff037a7f9509f3d248897a989715ef4b140",
      "-20_25": "b4cd1f0919589c76b413612464681d8eaff9aaf1c6ce33b4f66193a14901ae9c",
      "-20_26": "9b04d692a41f7fa10fd330a8647c858d24af182586af8b8d512452f4ee041ab7",
      "-20_27": "72fbcce4f6eba98d4401d49ed05f93683e4f7387d156347e73721c1525ebce55",
      "-20_28": "711536b8b39bcac86a31ed1b0414c7899f1fed5699f0c054393392d5a7215edb",
      "-20_29": "54fe8eaeba7bdee4c250c6eecb5f0971f623f263658992e212f6193b8c5e6d99",
      "-20_30": "8b9fb1c9a08ee5b253bcb41e8ee148c860068d8fc6304e8337f8f058e96ead7d",
      "-20_31": "9b2adba52d0b62bb125550b0d8db0f750710f4b401c05592c6bb993c6420a67d",
      "-20_32": "8a6d197b8fbdc5c11b5c7f256dae2c1264e858f204a798cef599e5075399e39b",
      "-20_33": "6d2680d864f687de0281823006ef009ab722104df4eb511ba0f46842f593ea51",
      "-20_34": "a002eff8514ab52017ecb076c4a91d05ebf1d7e343ef157b1ebe9309ab283b35",
      "-20_35": "82a221f5f45a4ab0dbd3b92603a79ed6ad395e400e58aee46981f1faaa99668b",
      "-19_19": "3ed1268ac46a8c4b73855ec34a8f6e4c23b1af49793a2f8095db027ff79c971b",
      "-19_20": "e2b5ad38fddc87f3cec82d593168619c4779114a0bc325c2779778a956962dd0",
      "-19_21": "6e1f284301403522a1f92876ac0f816ebd75ea6540d4d4a49d722d55181ed31a",
      "-19_22": "e74a0534164fb2ec5ab61631ce9dc032a92d69252aac578b590f6d116b5bcbd7",
      "-19_23": "da5c6c13c411c16630474ad03e2d6d0091b376d4823e6c32d0e9cead03f16acb",
      "-19_24": "6a1654373352ddcafc2a10ca52fa308aae9c99f5acf6208b6d758e0150b16548",
      "-19_25": "0b4d59f41a5d9b5856b22d01a16b7d0d364e700a79c39391cf16023b207f002a",
      "-19_26": "31233017e7e73de76c3e58ec7b20b86c9301bbf2542b660cdc330e3922098a40",
      "-19_27": "5847258c58d55b604319cb944dc27b1fe860c147386fb6705142a2e5f9f173f5",
      "-19_28": "a7610cd42ddff3bc5dcaaed7d3b7b6e892c415a7e8a57d8a6ef04e1ac05fa3b2",
      "-19_29": "0fc897f136ba1c30c98068dff4cfdfaf01f720a9f2b77492d63dff8c5bb047ad",
      "-19_30": "4ee7ee409e21bed24f87bba55a3e32bdffe8011fe0e34499a7ef14209d2dd316",
      "-19_31": "9c98dffa5f57ba222bd1d4252d5f7de3babb6793f3467325996ba7beda0f3575",
      "-19_32": "6cebe090f43fa4ec1ee5a2ebf4d71edcd0cc920a7237d420226b57afeff5fb3f",
      "-19_33": "31c2aa877b8b774aa422eba44c6a8984e6a137a7f83369cf2fe0d5710380ee95",
      "-19_34": "c1477ff2006f52266ad97a8fa26d13e4f8cc46800f317c20006c564d1d758ae3",
      "-19_35": "3715b6647cf729a6973d2602ec52914639fc566311e5e1ec9716007b2a5b7c48",
      "-18_18": "aaf98e07ab39b2dc41c1b87257b7ca90316987301dc55a2254533fc1e0fd0ded",
      "-18_19": "bd6649998473499e3cc806229a403d38a86505cd23bba913fe2adea89b223730",
      "-18_20": "582927afc6ce0681dad9d55c71a8ab2dcb2426c8e89afc949d0397474a6c6570",
      "-18_21": "9efdc0564442175e1cae50a9512b98b021b04ef4d98e2b7c4ac07a9887d292f0",
      "-18_22": "8cee810a128c91d4cbaba23bfae8d4d7e584b8de4cc2508487e9119c82f8a248",
      "-18_23": "952be58352c06a26f1af0abd6b139a33c0df7be6034022fc6c96822f61784fbd",
      "-18_24": "07550f04face788ceabb91856eafb32c5aaf6f75f9007c1c10f6fe973253e662",
      "-18_25": "f9b3329336f7a18f1a0323116fc862ebfec6b2bab02cf5396c58e93a363058ae",
      "-18_26": "028731db874ff825454b7bb1d43c2c32369e907bc54f95150b734246549a462a",
      "-18_27": "f6e75c40c7fb2abcd8fa0a71be8a4f4b9edc078431123143745638fdbe213cdd",
      "-18_28": "0657f526ba3829a5e34573ce753e217d7e32eb963c0c55e82bd55ec10a054fef",
      "-18_29": "c7e311ee28156af3ac316bb497672f4b9ec021d4c4239ca9779636137e061955",
      "-18_30": "1f84f8fa01661f9a9b3254259138fed4c7904feadb8539389b63dbe64e52ea22",
      "-18_31": "eb391b5d69c172823a08d75f6123b7f5d462fae578f2ed8c82e61e3483611b9b",
      "-18_32": "2c5279cdd501c135408c3e4d03f1538a0a00322b918722a4a3f5ac29b08e13e2",
      "-18_33": "6d2f943e73748de533192d2593e3908ce7ba72f286cb6c6e1bb98326c0b57f7c",
      "-18_34": "975660216bec204d739fbeab95e7512fb0ea4ca88e98597a52f8d95dc3f90011",
      "-18_35": "8ea4290d28327ab96b616bf784a54497969915de23a670aceff0c641f136ba5d",
      "-17_19": "f7064d8b823d5dcc975c7b646b90e91b6d3087e9f66c0ef5f1322a0dbe0ee39b",
      "-17_20": "3cb64e6e3d73c3d1b808e7eede198db1ed901926e4337770f3cbc11a72c60ab3",
      "-17_21": "a16028c19a701be8be3e2b86ce03ee081bb35b4c2cc55fbe0eab39adff3e7359",
      "-17_22": "d2bceed303ddd41f4eefc5b0377ea4c1328d1e5900d9276bd5f77fd428df300d",
      "-17_24": "67be403efe5555a7633d3deebb2853fdcf2c4396d1c638f218097cda932b415e",
      "-17_25": "238a2be2f277344d2494ab0c1aca3515f87293506cb4a8db0f4adb7b48d8144c",
      "-17_26": "a3159dd7b3ad6ecc8a5dfa6b9adbafc55c2592d06aaec7e537bc04ba2c460f5c",
      "-17_27": "3e782414d85f9ae03963248d9e80a2f8c8c7311ffcf8439ca589fac6917a4160",
      "-17_28": "0038dff94d27e2fa1e08a2819b550e08cdf725478d552cad4c5dec5281a70deb",
      "-17_29": "c9563b33503dd8b193dbe18518536a12df962bd363395c69099dec997b2a9adf",
      "-17_30": "cf3adbe277bda0c82aba8d84008479bc428048850370c1bd6f4ebe8e88756fee",
      "-17_31": "ca652356653b05adc8ffc50f2a3047958091f2f9eb7397b545a7797b1df842b2",
      "-17_32": "a0f716232e75e760c55f20cdf27a8cdba95ef0e333fe5d3fe3db6533c5677b55",
      "-17_33": "315208fbf460e2c736cfae035053d86201091892061edf540987f19c61a387af",
      "-16_29": "275dfd62f58e87b7d315298c832538028639bc26f3ab072aa66512b238920ed6",
      "-16_30": "08ad1140b2cb4a1fbaa8e0ae59850196fabe15361ce9968d145dd86b04545e88",
      "-16_31": "d56b14d61f66ed85e26b40d596967ce73acd81728ac636df1d670ae087ece636"
     }
    }
   }
  },
  "045811": {
   "name": "宮城県女川町",
   "short": "女川町",
   "engine": "gyoseiku",
   "data_version": "onagawa-fy2026-40f3453439c3",
   "status_key": "status:045811",
   "packs": {
    "core": {
     "key": "045811/core.9c59832ba91b.json.gz",
     "sha256": "9c59832ba91b41c8b79578694bb9c50ff92f6cc2453762bb912db2eecc012015",
     "bytes": 75199,
     "gzip_bytes": 13996
    }
   }
  }
 },
 "inputs": [
  {
   "path": "data/abr/mt_rsdtdsp_blk_pref17.csv",
   "url": "https://data.address-br.digital.go.jp/mt_rsdtdsp_blk/pref/mt_rsdtdsp_blk_pref17.csv.zip",
   "how": "ABR 住居表示 街区 (fetch_abr.py)"
  },
  {
   "path": "data/abr/mt_rsdtdsp_blk_pos_pref17.csv",
   "url": "https://data.address-br.digital.go.jp/mt_rsdtdsp_blk_pos/pref/mt_rsdtdsp_blk_pos_pref17.csv.zip",
   "how": "ABR 住居表示 街区の代表点 (fetch_abr.py)"
  },
  {
   "path": "data/abr/mt_parcel_city172014.csv",
   "url": "https://data.address-br.digital.go.jp/mt_parcel/city/mt_parcel_city172014.csv.zip",
   "how": "ABR 地番 (fetch_abr.py)"
  },
  {
   "path": "data/abr/mt_parcel_pos_city172014.csv",
   "url": "https://data.address-br.digital.go.jp/mt_parcel_pos/city/mt_parcel_pos_city172014.csv.zip",
   "how": "ABR 地番の代表点 (fetch_abr.py)"
  },
  {
   "path": "data/gsi_jukyo_17201.csv",
   "url": "https://saigai.gsi.go.jp/jusho/download/data/17201.zip",
   "how": "国土地理院 住居表示住所 (geo_peek.py で CSV にする)"
  },
  {
   "path": "data/estat_17201/r2ka17201.shp",
   "url": "https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A002005212020&code=17201&coordSys=1&format=shape&downloadType=5&datum=2011",
   "how": "e-Stat 国勢調査2020 小地域 (fetch_geo.py 17201)"
  },
  {
   "path": "data/kanazawa_rules.csv",
   "url": "https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/2/20102.html",
   "how": "金沢市の区域表 (fetch.py → kz_tables.py → kz_rules.py)"
  },
  {
   "path": "out/kanazawa_schedules.json",
   "url": "https://www4.city.kanazawa.lg.jp/soshikikarasagasu/gomigenryosuishinka/gyomuannai/1/3/2/20102.html",
   "how": "金沢市の地区別収集カレンダー (kz_calendar.py)"
  },
  {
   "path": "out/kanazawa_town_parts.geojson",
   "url": "",
   "how": "区域 (split_blocks.py)"
  },
  {
   "path": "out/kanazawa_land_border.geojson",
   "url": "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2024/N03-20240101_17_GML.zip",
   "how": "隣の市町との境界 (国土数値情報 N03-2024 石川県の金沢市の区域の線のうち、海岸線を除く部分)"
  },
  {
   "path": "out/kanazawa_town_alias.csv",
   "url": "",
   "how": "旧町名 (census_lineage.py ほか)"
  },
  {
   "path": "out/kanazawa_town_alias_target.csv",
   "url": "",
   "how": "旧町名の後継 (census_lineage.py ほか)"
  },
  {
   "path": "out/kanazawa_town_collisions.json",
   "url": "https://data.address-br.digital.go.jp/mt_town_fullset/mt_town_fullset_all.csv.zip",
   "how": "ほかの市区町村にもある町名 (build_town_collisions.py)"
  },
  {
   "path": "out/onagawa_data.json",
   "url": "https://www.town.onagawa.miyagi.jp/pdf/calendar/r08_chomin_calendar.pdf",
   "how": "女川町のカレンダー・分別表 (build_onagawa.py。出典は onagawa_data.json の sources)"
  }
 ]
};
