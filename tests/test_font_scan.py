"""font_scan 家族名归并 + 别名感知排除/PS 名解析的单元测试。

归并语义：同一字体的字重后缀家族（MiSans VF Bold）与多语言家族名
（Microsoft YaHei / 微软雅黑）各归并为单一规范名，被隐藏的别名仍是
真实 Qt 家族名——旧项目数据按原名存取不受影响，仅下拉列表与排除
过滤按规范名呈现（对齐 Photoshop 的组织方式，canonical 优先中文名）。
"""

import unittest

from qtpy.QtWidgets import QApplication

from utils import font_scan, shared


def _make_lookup(styles_map, weight_of_style):
    """构造可注入的 styles_of / weight_of 查找对（避免依赖真实字体库）。"""

    def styles_of(family):
        return styles_map.get(family, [])

    def weight_of(family, style):
        return weight_of_style[(family, style)]

    return styles_of, weight_of


class SplitWeightFamilyNameTest(unittest.TestCase):
    def test_recognized_suffixes(self):
        self.assertEqual(
            font_scan.split_weight_family_name("MiSans VF Bold"), ("MiSans VF", 700)
        )
        self.assertEqual(
            font_scan.split_weight_family_name("Inter Display SemiBold"),
            ("Inter Display", 600),
        )
        self.assertEqual(
            font_scan.split_weight_family_name("MiSans VF Regular"),
            ("MiSans VF", 400),
        )

    def test_non_weight_names_untouched(self):
        # 无字重后缀（大小写混合词、非后缀词尾）不得拆分
        self.assertEqual(font_scan.split_weight_family_name("Blackadder ITC"), (None, None))
        self.assertEqual(font_scan.split_weight_family_name("Arial"), (None, None))
        # 'Semilight' 不是已识别字重词（上游同语义），不得按 'light' 误拆
        self.assertEqual(
            font_scan.split_weight_family_name("Malgun Gothic Semilight"), (None, None)
        )


class WeightSuffixAliasesTest(unittest.TestCase):
    def test_merges_weight_variant_family(self):
        styles = {"MiSans VF": ["Regular", "Bold"], "MiSans VF Bold": ["Regular"]}
        weights = {
            ("MiSans VF", "Regular"): 400,
            ("MiSans VF", "Bold"): 700,
            ("MiSans VF Bold", "Regular"): 700,
        }
        styles_of, weight_of = _make_lookup(styles, weights)
        aliases = font_scan.weight_suffix_aliases(list(styles), styles_of, weight_of)
        self.assertEqual(aliases, {"MiSans VF Bold": "MiSans VF"})

    def test_buckets_off_standard_actual_weights(self):
        """实际字重偏离名字暗示值时按就近分桶比较（上游语义）。

        DirectWrite 实测：'MiSans VF Semibold' 实际字重 630（= DemiBold 桶）。
        """
        styles = {"MiSans VF": ["Regular", "Demibold"], "MiSans VF Semibold": ["Regular"]}
        weights = {
            ("MiSans VF", "Regular"): 400,
            ("MiSans VF", "Demibold"): 600,
            ("MiSans VF Semibold", "Regular"): 630,
        }
        styles_of, weight_of = _make_lookup(styles, weights)
        aliases = font_scan.weight_suffix_aliases(list(styles), styles_of, weight_of)
        self.assertEqual(aliases, {"MiSans VF Semibold": "MiSans VF"})

    def test_no_merge_when_bucket_differs_from_name(self):
        # 名字说 Bold(700) 但实际 630 分桶后是 600 → 与名字桶不符，不归并
        styles = {"MiSans VF": ["Regular", "Bold"], "MiSans VF Bold": ["Regular"]}
        weights = {
            ("MiSans VF", "Regular"): 400,
            ("MiSans VF", "Bold"): 700,
            ("MiSans VF Bold", "Regular"): 630,
        }
        styles_of, weight_of = _make_lookup(styles, weights)
        aliases = font_scan.weight_suffix_aliases(list(styles), styles_of, weight_of)
        self.assertEqual(aliases, {})

    def test_keeps_family_when_base_lacks_weight(self):
        # Arial Black：基家族 Arial 无 900 字重样式 → 独立家族不归并
        styles = {"Arial": ["Regular", "Bold"], "Arial Black": ["Regular"]}
        weights = {
            ("Arial", "Regular"): 400,
            ("Arial", "Bold"): 700,
            ("Arial Black", "Regular"): 900,
        }
        styles_of, weight_of = _make_lookup(styles, weights)
        aliases = font_scan.weight_suffix_aliases(list(styles), styles_of, weight_of)
        self.assertEqual(aliases, {})

    def test_keeps_orphan_suffix_family(self):
        styles = {"Orphan Bold": ["Regular"]}
        weights = {("Orphan Bold", "Regular"): 700}
        styles_of, weight_of = _make_lookup(styles, weights)
        aliases = font_scan.weight_suffix_aliases(list(styles), styles_of, weight_of)
        self.assertEqual(aliases, {})


class GroupLocalizedAliasesTest(unittest.TestCase):
    @staticmethod
    def _face(ps, weight, names):
        # names: {名字: langID 或 langID 集} → 转成 face 记录结构
        return {
            "ps": ps,
            "weight": weight,
            "names": {
                n: ({ls} if isinstance(ls, int) else set(ls)) for n, ls in names.items()
            },
        }

    def test_prefers_zh_name_as_canonical(self):
        faces = [
            self._face("MSYH", 400, {"Microsoft YaHei": 0x409, "微软雅黑": 0x804}),
            self._face("MSYHBD", 700, {"Microsoft YaHei": 0x409, "微软雅黑": 0x804}),
        ]
        aliases = font_scan.group_localized_aliases(["Microsoft YaHei", "微软雅黑"], faces)
        self.assertEqual(aliases, {"Microsoft YaHei": "微软雅黑"})

    def test_merges_typographic_family_names(self):
        """nameID 16 排版家族与 nameID 1 家族同 face 时归并（RiiPopkaku 实测）。"""
        faces = [
            self._face("RiiPopkaku-R", 400, {"RiiPopkaku-R": 0x409, "RiiPopkaku": 0x409}),
        ]
        aliases = font_scan.group_localized_aliases(["RiiPopkaku", "RiiPopkaku-R"], faces)
        # 同语言取最短 → 排版家族名胜出
        self.assertEqual(aliases, {"RiiPopkaku-R": "RiiPopkaku"})

    def test_falls_back_to_en_then_other(self):
        # 无 zh 名：en 优先于其他语言
        faces = [self._face("P", 400, {"BB": 0x409, "AA": 0x411})]
        aliases = font_scan.group_localized_aliases(["AA", "BB"], faces)
        self.assertEqual(aliases, {"AA": "BB"})

        # zh 与 en 都缺失时取最短名
        faces = [self._face("P", 400, {"Longer": 0x411, "Short": 0x404})]
        aliases = font_scan.group_localized_aliases(["Longer", "Short"], faces)
        self.assertEqual(aliases, {"Longer": "Short"})

    def test_different_face_sets_not_merged(self):
        # DengXian 与 DengXian Light 的 face 集不同（Light 是独立 face），
        # 不得因名字相似归并——字重归并是 weight_suffix_aliases 的职责
        faces = [
            self._face("DX", 400, {"DengXian": 0x409, "等线": 0x804}),
            self._face("DXL", 300, {"DengXian Light": 0x409, "等线 Light": 0x804}),
        ]
        aliases = font_scan.group_localized_aliases(
            ["DengXian", "DengXian Light", "等线", "等线 Light"], faces
        )
        self.assertEqual(aliases, {"DengXian": "等线", "DengXian Light": "等线 Light"})

    def test_family_without_faces_untouched(self):
        faces = [self._face("P", 400, {"Known": 0x409})]
        aliases = font_scan.group_localized_aliases(["Known", "Unknown"], faces)
        self.assertEqual(aliases, {})


class ResolveAliasChainsTest(unittest.TestCase):
    def test_two_level_chain(self):
        # 字重别名指向的基家族又本身是语言别名：'X Bold' → 'X' → '中文名'
        chains = font_scan.resolve_alias_chains({"X Bold": "X", "X": "中文名"})
        self.assertEqual(chains, {"X Bold": "中文名", "X": "中文名"})

    def test_no_cycles(self):
        chains = font_scan.resolve_alias_chains({"A": "B", "B": "A"})
        self.assertIn(chains["A"], ("A", "B"))
        self.assertIn(chains["B"], ("A", "B"))


class SharedAliasFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # 隔离 pcfg.simplified_font_map：全量跑时其他测试文件 import 期
        # load_config 载入真实用户配置，映射里的名字会短路别名扩展语义
        from utils.config import pcfg

        self._pcfg_orig = dict(pcfg.simplified_font_map)
        pcfg.simplified_font_map = {}
        self._orig = (
            shared.ALL_FONT_FAMILIES,
            shared.FONT_FAMILY_ALIAS,
            shared.FONT_PS_NAMES,
        )

    def tearDown(self):
        from utils.config import pcfg

        pcfg.simplified_font_map = self._pcfg_orig
        shared.ALL_FONT_FAMILIES, shared.FONT_FAMILY_ALIAS, shared.FONT_PS_NAMES = self._orig

    def test_canonical_font_family(self):
        shared.FONT_FAMILY_ALIAS = {"鸿蒙黑体": ["HarmonyOS Sans SC"]}
        self.assertEqual(shared.canonical_font_family("HarmonyOS Sans SC"), "鸿蒙黑体")
        self.assertEqual(shared.canonical_font_family("鸿蒙黑体"), "鸿蒙黑体")
        # 未知名字（自定义字体/离屏环境）原样返回
        self.assertEqual(shared.canonical_font_family("No Such Font"), "No Such Font")

    def test_exclusion_expands_aliases(self):
        """排除名是被隐藏的别名时视为排除其规范名（旧排除表继续生效）。"""
        shared.ALL_FONT_FAMILIES = ["鸿蒙黑体", "MiSans VF", "Other"]
        shared.FONT_FAMILY_ALIAS = {
            "鸿蒙黑体": ["HarmonyOS Sans SC"],
            "MiSans VF": ["MiSans VF Bold"],
        }
        # 排除的是旧英文名 → 规范名一并隐藏
        self.assertEqual(
            shared.get_filtered_font_list(["HarmonyOS Sans SC"]), ["MiSans VF", "Other"]
        )
        # 排除的是旧字重变体名 → 基家族一并隐藏
        self.assertEqual(
            shared.get_filtered_font_list(["MiSans VF Bold"]), ["鸿蒙黑体", "Other"]
        )
        self.assertEqual(
            shared.get_filtered_font_list([]), ["鸿蒙黑体", "MiSans VF", "Other"]
        )

    def test_init_font_list_shapes(self):
        """真实枚举冒烟：归并后列表与别名互斥，样式表覆盖别名。"""
        shared.init_font_list()
        display = set(shared.ALL_FONT_FAMILIES)
        self.assertEqual(shared.ALL_FONT_FAMILIES, sorted(display))
        aliases = set()
        for canonical, names in shared.FONT_FAMILY_ALIAS.items():
            self.assertIn(canonical, display)
            aliases.update(names)
        self.assertFalse(display & aliases)
        for family in display | aliases:
            self.assertIn(family, shared.FONT_STYLES)


class ComputeSimplifyMapTest(unittest.TestCase):
    """「一键精简」激进规则（fork 私有，与默认归并分开验证）。"""

    @staticmethod
    def _face(ps, weight, names):
        return {
            "ps": ps,
            "weight": weight,
            "names": {
                n: ({ls} if isinstance(ls, int) else set(ls))
                for n, ls in names.items()
            },
        }

    def _compute(self, families, styles, weights, faces):
        styles_of, weight_of = _make_lookup(styles, weights)
        return font_scan.compute_simplify_map(
            families=list(families),
            faces=faces,
            styles_of=styles_of,
            weight_of=weight_of,
        )

    def test_attached_suffix_and_trailing_spaces(self):
        """攸望系：后缀紧跟全角括号（无空格）且家族名带尾随空格。"""
        families = [
            "攸望竹带体（简繁）",
            "攸望竹带体（简繁）Medium  ",
            "YW ZhuDaiTi",
            "YW ZhuDaiTi Medium",
        ]
        styles = {
            "攸望竹带体（简繁）": ["Regular", "Medium", "Heavy"],
            "攸望竹带体（简繁）Medium  ": ["Regular"],
            "YW ZhuDaiTi": ["Regular", "Medium", "Heavy"],
            "YW ZhuDaiTi Medium": ["Regular"],
        }
        weights = {
            ("攸望竹带体（简繁）", "Regular"): 400,
            ("攸望竹带体（简繁）", "Medium"): 500,
            ("攸望竹带体（简繁）", "Heavy"): 800,
            ("攸望竹带体（简繁）Medium  ", "Regular"): 500,
            ("YW ZhuDaiTi", "Regular"): 400,
            ("YW ZhuDaiTi", "Medium"): 500,
            ("YW ZhuDaiTi", "Heavy"): 800,
            ("YW ZhuDaiTi Medium", "Regular"): 500,
        }
        faces = [
            self._face(
                "YWZhuDaiTi-Regular",
                400,
                {"YW ZhuDaiTi": 0x409, "攸望竹带体（简繁）": 0x804},
            ),
            # name 表里的键没有尾随空格（写入时被 strip）；基家族名
            # 同时出现在每个 face 上（与真实攸望字体一致）
            self._face(
                "YWZhuDaiTi-Medium",
                500,
                {
                    "YW ZhuDaiTi": 0x409,
                    "YW ZhuDaiTi Medium": 0x409,
                    "攸望竹带体（简繁）": 0x804,
                    "攸望竹带体（简繁）Medium": 0x804,
                },
            ),
        ]
        result = self._compute(families, styles, weights, faces)
        self.assertEqual(
            result, {"攸望竹带体（简繁）Medium  ": "攸望竹带体（简繁）"}
        )

    def test_vf_instance_folds_despite_bucket_mismatch(self):
        """鸿蒙系：VF 命名实例家族无 name 表记录，按样式同名折入，
        实际字重偏离名字暗示值（Light 247/Black 844）不阻碍。"""
        base_styles = ["Regular", "Light", "Black"]
        families = [
            "鸿蒙黑体",
            "HarmonyOS Sans SC",
            "HarmonyOS Sans SC Light",
            "HarmonyOS Sans SC Black",
        ]
        styles = {
            "鸿蒙黑体": base_styles,
            "HarmonyOS Sans SC": base_styles,
            "HarmonyOS Sans SC Light": ["Regular"],
            "HarmonyOS Sans SC Black": ["Regular"],
        }
        weights = {
            ("鸿蒙黑体", "Regular"): 400,
            ("鸿蒙黑体", "Light"): 247,
            ("鸿蒙黑体", "Black"): 844,
            ("HarmonyOS Sans SC", "Regular"): 400,
            ("HarmonyOS Sans SC", "Light"): 247,
            ("HarmonyOS Sans SC", "Black"): 844,
            ("HarmonyOS Sans SC Light", "Regular"): 247,
            ("HarmonyOS Sans SC Black", "Regular"): 844,
        }
        faces = [
            self._face(
                "HarmonyOS_Sans_SC",
                400,
                {"鸿蒙黑体": 0x804, "HarmonyOS Sans SC": 0x409},
            )
        ]
        result = self._compute(families, styles, weights, faces)
        self.assertEqual(
            result,
            {
                "HarmonyOS Sans SC Light": "鸿蒙黑体",
                "HarmonyOS Sans SC Black": "鸿蒙黑体",
            },
        )

    def test_containment_folds_language_variant(self):
        """'YW YuanTi' 的 face 集被中文基家族包含 → 并入（规则二）。"""
        families = ["攸望圆体（简繁）", "YW YuanTi", "YW YuanTi Medium"]
        styles = {
            "攸望圆体（简繁）": [
                "Regular", "Medium", "Light", "SemiBold", "Bold", "Heavy",
            ],
            "YW YuanTi": ["Light", "SemiBold", "Bold", "Heavy"],
            "YW YuanTi Medium": ["Regular"],
        }
        weights = {
            ("攸望圆体（简繁）", "Regular"): 400,
            ("攸望圆体（简繁）", "Medium"): 500,
            ("攸望圆体（简繁）", "Light"): 300,
            ("攸望圆体（简繁）", "SemiBold"): 600,
            ("攸望圆体（简繁）", "Bold"): 700,
            ("攸望圆体（简繁）", "Heavy"): 800,
            ("YW YuanTi", "Light"): 300,
            ("YW YuanTi", "SemiBold"): 600,
            ("YW YuanTi", "Bold"): 700,
            ("YW YuanTi", "Heavy"): 800,
            ("YW YuanTi Medium", "Regular"): 500,
        }
        faces = [
            self._face(
                "YWYuanTi-Medium",
                500,
                {"YW YuanTi Medium": 0x409, "攸望圆体（简繁）": 0x804},
            ),
            self._face("YWYuanTi-Regular", 400, {"攸望圆体（简繁）": 0x804, "YW YuanTi": 0x409}),
        ]
        result = self._compute(families, styles, weights, faces)
        self.assertEqual(
            result,
            {
                "YW YuanTi": "攸望圆体（简繁）",
                "YW YuanTi Medium": "攸望圆体（简繁）",
            },
        )

    def test_guards_keep_distinct_designs(self):
        """基家族无同名字样（Arial Black）或别名 face 不在基家族内
        （字重实际值偏离导致默认归并拒绝的独立 face）都不得折入。"""
        families = ["Arial", "Arial Black", "X", "X Heavy", "Y", "Y Bold"]
        styles = {
            "Arial": ["Regular", "Bold"],
            "Arial Black": ["Regular"],
            "X": ["Regular", "Heavy"],
            "X Heavy": ["Regular"],
            "Y": ["Regular", "Bold"],
            "Y Bold": ["Regular"],
        }
        weights = {
            ("Arial", "Regular"): 400,
            ("Arial", "Bold"): 700,
            ("Arial Black", "Regular"): 900,
            ("X", "Regular"): 400,
            ("X", "Heavy"): 800,
            ("X Heavy", "Regular"): 800,
            ("Y", "Regular"): 400,
            ("Y", "Bold"): 700,
            ("Y Bold", "Regular"): 630,
        }
        faces = [
            self._face("X-Regular", 400, {"X": 0x409}),
            self._face("X-Heavy", 800, {"X": 0x409, "X Heavy": 0x409}),
            # 'Y Bold' 家族指向独立 face，不在 Y 的 face 集内
            self._face("YBold-Other", 630, {"Y Bold": 0x409}),
            self._face("Y-Regular", 400, {"Y": 0x409}),
        ]
        result = self._compute(families, styles, weights, faces)
        # 'Arial Black'：基家族无 Black 样式 → 保留
        # 'Y Bold'：实际字重 630 使默认归并拒绝，但精简规则靠样式同名
        #   覆盖本可折入——face 子集守卫发现别名 face 不在 Y 内 → 保留
        # 'X Heavy'：heavy 不在上游词表，默认归并不处理；精简规则折入
        self.assertEqual(result, {"X Heavy": "X"})


class SharedSimplifyMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from utils.config import pcfg

        self._pcfg_orig = dict(pcfg.simplified_font_map)
        # 起始清空：全量跑时其他测试文件 import 期 load_config 载入的
        # 真实映射不该泄漏进本类用例（各用例自行赋值所需状态）
        pcfg.simplified_font_map = {}
        self._orig = (
            shared.ALL_FONT_FAMILIES,
            shared.FONT_FAMILY_ALIAS,
            shared.FONT_STYLES,
            shared.FONT_PS_NAMES,
        )

    def tearDown(self):
        from utils.config import pcfg

        pcfg.simplified_font_map = self._pcfg_orig
        (
            shared.ALL_FONT_FAMILIES,
            shared.FONT_FAMILY_ALIAS,
            shared.FONT_STYLES,
            shared.FONT_PS_NAMES,
        ) = self._orig

    def test_simplified_alias_hides_itself_only(self):
        """精简别名（带标记映射）只隐藏自身，不扩展隐藏规范名——
        精简条目与手动排除同住 excluded_fonts 时的关键语义差。"""
        from utils.config import pcfg

        pcfg.simplified_font_map = {"YW YuanTi Medium  ": "攸望圆体（简繁）"}
        shared.ALL_FONT_FAMILIES = [
            "Other", "攸望圆体（简繁）", "YW YuanTi Medium  ",
        ]
        excluded = ["YW YuanTi Medium  "]
        self.assertEqual(
            shared.get_filtered_font_list(excluded),
            ["Other", "攸望圆体（简繁）"],
        )

    def test_lost_marker_falls_back_to_expansion(self):
        """标记映射丢失时（excluded 里只剩裸别名）退回默认扩展语义——
        提醒标记映射必须与 excluded_fonts 同盘保存。"""
        from utils.config import pcfg

        pcfg.simplified_font_map = {}
        shared.ALL_FONT_FAMILIES = [
            "Other", "攸望圆体（简繁）", "YW YuanTi Medium  ",
        ]
        shared.FONT_FAMILY_ALIAS = {"攸望圆体（简繁）": ["YW YuanTi Medium  "]}
        self.assertEqual(
            shared.get_filtered_font_list(["YW YuanTi Medium  "]), ["Other"]
        )

    def test_init_font_list_borrows_ps_names(self):
        """init_font_list 为精简别名借规范名的 PS 记录（patch 枚举，
        离屏可测）；显示列表不再在 init 时剔除。"""
        from unittest.mock import patch

        from utils.config import pcfg

        fake = {
            "display_families": ["Other", "攸望圆体（简繁）", "YW YuanTi Medium  "],
            "alias_to_canonical": {"YW YuanTi Medium  ": "攸望圆体（简繁）"},
            "canonical_to_aliases": {"攸望圆体（简繁）": ["YW YuanTi Medium  "]},
            "styles": {
                "攸望圆体（简繁）": ["Regular"],
                "YW YuanTi Medium  ": ["Regular"],
                "Other": ["Regular"],
            },
            "ps_index": {"攸望圆体（简繁）": {400: "PS1"}},
        }
        pcfg.simplified_font_map = {"YW YuanTi Medium  ": "攸望圆体（简繁）"}
        with patch("utils.font_scan.build_font_data", return_value=fake):
            shared.init_font_list()

        # init 不剔除，过滤交给 get_filtered_font_list（会话内即时生效）
        self.assertIn("YW YuanTi Medium  ", shared.ALL_FONT_FAMILIES)
        # 别名回显归到规范名
        self.assertEqual(
            shared.canonical_font_family("YW YuanTi Medium  "), "攸望圆体（简繁）"
        )
        self.assertIn("YW YuanTi Medium  ", shared.FONT_STYLES)
        # PS 索引借规范名的记录
        self.assertEqual(
            shared.FONT_PS_NAMES["YW YuanTi Medium  "], {400: "PS1"}
        )

    def test_canonical_maps_for_display(self):
        from utils.config import pcfg

        pcfg.simplified_font_map = {"YW YuanTi": "攸望圆体（简繁）"}
        # 旧数据存的别名 → 回显规范名
        self.assertEqual(
            shared.canonical_font_family("YW YuanTi"), "攸望圆体（简繁）"
        )

    def test_config_round_trip(self):
        """标记映射随 config.json 往返（落盘契约，用户报过的回归点）。"""
        import json
        import tempfile

        from utils.config import ProgramConfig, json_dump_program_config

        # 全新实例做序列化：全局 pcfg 可能被其他测试文件 import 期
        # load_config 载成真实用户配置（module params 已是落盘扁平形，
        # get_saving_params 取 ["value"] 会 KeyError）
        cfg = ProgramConfig()
        cfg.simplified_font_map = {"YW YuanTi": "攸望圆体（简繁）"}
        payload = json.loads(json_dump_program_config(cfg))
        self.assertEqual(
            payload["simplified_font_map"], {"YW YuanTi": "攸望圆体（简繁）"}
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(payload, f, ensure_ascii=False)
            path = f.name
        try:
            loaded = ProgramConfig.load(path)
            self.assertEqual(
                loaded.simplified_font_map, {"YW YuanTi": "攸望圆体（简繁）"}
            )
        finally:
            import os

            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
