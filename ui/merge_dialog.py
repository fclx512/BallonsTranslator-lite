from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
)

from ui.custom_widget import NoArrowsSpinBox


class MergeDialog(QDialog):
    # 定义信号：当用户点击运行按钮时发出
    run_current_clicked = Signal()  # 对当前文件运行
    run_all_clicked = Signal()  # 对所有文件运行

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Region Merge Tool Settings"))
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.adjustSize()
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(6)
        self.layout.setContentsMargins(8, 8, 8, 8)

        # --- Main Settings --- #
        main_group = QGroupBox(self.tr("Main Settings"))
        main_layout = QFormLayout(main_group)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(8, 6, 8, 6)

        self.merge_mode = QComboBox()
        self.merge_mode.addItem(self.tr("Vertical Merge"), "VERTICAL")
        self.merge_mode.addItem(self.tr("Horizontal Merge"), "HORIZONTAL")
        self.merge_mode.addItem(
            self.tr("Vertical Then Horizontal"), "VERTICAL_THEN_HORIZONTAL"
        )
        self.merge_mode.addItem(
            self.tr("Horizontal Then Vertical"), "HORIZONTAL_THEN_VERTICAL"
        )
        self.merge_mode.addItem(self.tr("None"), "NONE")
        main_layout.addRow(self.tr("Merge Mode:"), self.merge_mode)
        self.layout.addWidget(main_group)

        # --- Text Reading Order Settings ---
        reading_order_group = QGroupBox(self.tr("Text Reading Order (by Label)"))
        reading_order_layout = QFormLayout(reading_order_group)
        reading_order_layout.setSpacing(4)
        reading_order_layout.setContentsMargins(8, 6, 8, 6)

        self.ltr_labels_edit = QLineEdit()
        self.ltr_labels_edit.setPlaceholderText(self.tr("label1,label2,..."))
        self.rtl_labels_edit = QLineEdit()
        self.rtl_labels_edit.setText("balloon,qipao,shuqing")
        self.ttb_labels_edit = QLineEdit()
        self.ttb_labels_edit.setText("changfangtiao,hengxie")

        reading_order_layout.addRow(
            self.tr("Left-to-Right (LTR) labels:"), self.ltr_labels_edit
        )
        reading_order_layout.addRow(
            self.tr("Right-to-Left (RTL) labels:"), self.rtl_labels_edit
        )
        reading_order_layout.addRow(
            self.tr("Top-to-Bottom (TTB) labels:"), self.ttb_labels_edit
        )

        self.layout.addWidget(reading_order_group)

        # --- Labeling Rules --- #
        label_group = QGroupBox(self.tr("Label Merge Rules"))
        label_layout = QFormLayout(label_group)
        label_layout.setSpacing(4)
        label_layout.setContentsMargins(8, 6, 8, 6)

        self.label_merge_strategy = QComboBox()
        self.label_merge_strategy.addItem(
            self.tr("Prefer Shorter Label"), "PREFER_SHORTER"
        )
        self.label_merge_strategy.addItem(self.tr("Use First Block's Label"), "FIRST")
        self.label_merge_strategy.addItem(
            self.tr("Combine Labels (label1+label2)"), "COMBINE"
        )
        self.label_merge_strategy.addItem(
            self.tr("Prefer Non-Default Label"), "PREFER_NON_DEFAULT"
        )
        label_layout.addRow(self.tr("Label Merge Strategy:"), self.label_merge_strategy)

        self.enable_exclude_labels = QCheckBox(
            self.tr("Enable label exclusion (blacklist)")
        )
        self.enable_exclude_labels.setChecked(True)
        label_layout.addRow(self.enable_exclude_labels)

        self.exclude_labels = QLineEdit()
        self.exclude_labels.setText("other")
        self.exclude_labels.setPlaceholderText(self.tr("e.g., label1,label2"))
        label_layout.addRow(self.tr("Blacklist Labels:"), self.exclude_labels)

        self.enable_exclude_labels.toggled.connect(self.exclude_labels.setEnabled)

        self.require_same_label = QCheckBox(
            self.tr("Require identical labels to merge")
        )
        label_layout.addRow(self.require_same_label)

        self.use_specific_groups = QCheckBox(
            self.tr("Merge only within specific label groups")
        )
        self.specific_groups_edit = QPlainTextEdit()
        self.specific_groups_edit.setPlaceholderText(
            self.tr(
                "One group per line, labels separated by commas\ne.g.:\nballoon,balloon2\nqipao,qipao2"
            )
        )
        self.specific_groups_edit.setPlainText(
            "balloon\nqipao\nshuqing\nchangfangtiao\nhengxie"
        )
        self.specific_groups_edit.setMinimumHeight(100)
        self.specific_groups_edit.setMaximumHeight(120)
        self.specific_groups_edit.setEnabled(False)
        self.use_specific_groups.toggled.connect(self.specific_groups_edit.setEnabled)
        self.use_specific_groups.toggled.connect(
            lambda checked: self.require_same_label.setDisabled(checked)
        )

        label_layout.addRow(self.use_specific_groups)
        label_layout.addRow(self.specific_groups_edit)

        self.layout.addWidget(label_group)

        # --- Geometric Rules ---
        geo_group = QGroupBox(self.tr("Geometric Merge Parameters"))
        geo_layout = QFormLayout(geo_group)
        geo_layout.setSpacing(4)
        geo_layout.setContentsMargins(8, 6, 8, 6)

        self.max_vertical_gap = NoArrowsSpinBox()
        self.max_vertical_gap.setRange(0, 1000)
        self.max_vertical_gap.setValue(10)
        self.min_width_overlap_ratio = NoArrowsSpinBox()
        self.min_width_overlap_ratio.setRange(0, 100)
        self.min_width_overlap_ratio.setValue(90)
        self.min_width_overlap_ratio.setSuffix(" %")

        self.max_horizontal_gap = NoArrowsSpinBox()
        self.max_horizontal_gap.setRange(0, 1000)
        self.max_horizontal_gap.setValue(10)
        self.min_height_overlap_ratio = NoArrowsSpinBox()
        self.min_height_overlap_ratio.setRange(0, 100)
        self.min_height_overlap_ratio.setValue(90)
        self.min_height_overlap_ratio.setSuffix(" %")

        geo_layout.addRow(QLabel(self.tr("<b>Vertical Merge (Up-Down)</b>")))
        geo_layout.addRow(self.tr("Max Vertical Gap (px):"), self.max_vertical_gap)
        geo_layout.addRow(
            self.tr("Min Horizontal Overlap Ratio:"), self.min_width_overlap_ratio
        )
        geo_layout.addRow(QLabel(self.tr("<b>Horizontal Merge (Left-Right)</b>")))
        geo_layout.addRow(self.tr("Max Horizontal Gap (px):"), self.max_horizontal_gap)
        geo_layout.addRow(
            self.tr("Min Vertical Overlap Ratio:"), self.min_height_overlap_ratio
        )

        self.layout.addWidget(geo_group)

        # --- Advanced Options --- #
        advanced_group = QGroupBox(self.tr("Advanced Options"))
        advanced_layout = QVBoxLayout(advanced_group)
        advanced_layout.setSpacing(4)
        advanced_layout.setContentsMargins(8, 6, 8, 6)
        self.allow_negative_gap = QCheckBox(
            self.tr("Allow negative gap (overlapping blocks)")
        )
        self.allow_negative_gap.setChecked(True)
        advanced_layout.addWidget(self.allow_negative_gap)

        self.layout.addWidget(advanced_group)

        # --- Merge Result Type --- #
        result_type_group = QGroupBox(self.tr("Merge Result Type"))
        result_type_layout = QVBoxLayout(result_type_group)
        result_type_layout.setSpacing(4)
        result_type_layout.setContentsMargins(8, 6, 8, 6)

        self.output_type_group = QButtonGroup(self)
        self.radio_output_rectangle = QRadioButton(self.tr("Axis-Aligned Rectangle"))
        self.radio_output_rotation = QRadioButton(self.tr("Rotated Rectangle"))

        self.radio_output_rectangle.setChecked(True)

        self.output_type_group.addButton(self.radio_output_rectangle, 1)
        self.output_type_group.addButton(self.radio_output_rotation, 2)

        result_type_layout.addWidget(self.radio_output_rectangle)
        result_type_layout.addWidget(self.radio_output_rotation)

        self.layout.addWidget(result_type_group)

        # --- Buttons --- #
        button_layout = QHBoxLayout()
        self.run_current_button = QPushButton(self.tr("Run on Current File"))
        self.run_all_button = QPushButton(self.tr("Run on All Files"))
        self.cancel_button = QPushButton(self.tr("Cancel"))

        button_layout.addWidget(self.run_current_button)
        button_layout.addWidget(self.run_all_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch()

        # 连接按钮信号
        self.run_current_button.clicked.connect(self.on_run_current)
        self.run_all_button.clicked.connect(self.on_run_all)
        self.cancel_button.clicked.connect(self.reject)

        self.layout.addLayout(button_layout)

    def on_run_current(self):
        """Run merge on current file"""
        self.run_current_clicked.emit()
        # 不关闭对话框，让用户可以继续调整参数

    def on_run_all(self):
        """Run merge on all files"""
        self.run_all_clicked.emit()
        # 不关闭对话框，让用户可以继续调整参数

    def get_config(self):
        """Get user-configured merge params"""
        config = {}
        config["MERGE_MODE"] = self.merge_mode.currentData()
        # Set a default reading direction, as the UI for a global default has been removed.
        # The logic in merger.py uses this as a fallback.
        config["READING_DIRECTION"] = "LTR"

        # Parse per-label directions from the new QLineEdits
        per_label_directions = {}
        for label in [
            label.strip()
            for label in self.ltr_labels_edit.text().split(",")
            if label.strip()
        ]:
            per_label_directions[label] = "LTR"
        for label in [
            label.strip()
            for label in self.rtl_labels_edit.text().split(",")
            if label.strip()
        ]:
            per_label_directions[label] = "RTL"
        for label in [
            label.strip()
            for label in self.ttb_labels_edit.text().split(",")
            if label.strip()
        ]:
            per_label_directions[label] = "TTB"
        config["PER_LABEL_DIRECTIONS"] = per_label_directions

        # 只有当黑名单启用时才使用排除标签
        if self.enable_exclude_labels.isChecked():
            excluded = self.exclude_labels.text().strip()
            config["LABELS_TO_EXCLUDE_FROM_MERGE"] = set(
                label.strip() for label in excluded.split(",") if label.strip()
            )
        else:
            config["LABELS_TO_EXCLUDE_FROM_MERGE"] = set()

        config["USE_SPECIFIC_MERGE_GROUPS"] = self.use_specific_groups.isChecked()
        if config["USE_SPECIFIC_MERGE_GROUPS"]:
            groups_text = self.specific_groups_edit.toPlainText().strip()
            groups = []
            for line in groups_text.split("\n"):
                if line.strip():
                    groups.append([label.strip() for label in line.split(",")])
            config["SPECIFIC_MERGE_GROUPS"] = groups
            config["REQUIRE_SAME_LABEL"] = False  # This is disabled in UI
        else:
            config["SPECIFIC_MERGE_GROUPS"] = []
            config["REQUIRE_SAME_LABEL"] = self.require_same_label.isChecked()

        config["LABEL_MERGE_STRATEGY"] = self.label_merge_strategy.currentData()

        config["VERTICAL_MERGE_PARAMS"] = {
            "max_vertical_gap": self.max_vertical_gap.value(),
            "min_width_overlap_ratio": self.min_width_overlap_ratio.value(),
            "overlap_epsilon": 1e-6,
        }

        config["HORIZONTAL_MERGE_PARAMS"] = {
            "max_horizontal_gap": self.max_horizontal_gap.value(),
            "min_height_overlap_ratio": self.min_height_overlap_ratio.value(),
            "overlap_epsilon": 1e-6,
        }

        config["ADVANCED_MERGE_OPTIONS"] = {
            "allow_negative_gap": self.allow_negative_gap.isChecked(),
            "debug_mode": False,  # Not exposed in UI
        }

        config["OUTPUT_SHAPE_TYPE"] = (
            "rectangle" if self.output_type_group.checkedId() == 1 else "rotation"
        )

        return config
