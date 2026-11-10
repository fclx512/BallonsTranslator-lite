from qtpy.QtCore import QCoreApplication, QThread, Signal
from qtpy.QtGui import QImage, QPixmap

from utils.io_utils import imwrite
from utils.message import create_error_dialog
from utils.proj_imgtrans import ProjImgTrans

from .custom_widget import ProgressMessageBox
from .misc import pixmap2ndarray


class ThreadBase(QThread):
    _thread_exception_type = None
    # Subclasses override this with an explicit-context translate at the
    # literal (runtime tr context is the subclass, not ThreadBase, so the
    # base default is intentionally left untranslated).
    _thread_error_msg = "Thread job failed."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = None

    def on_exec_failed(self):
        return

    def run(self):
        if self.job is not None:
            try:
                self.job()
            except Exception as e:
                self.on_exec_failed()
                create_error_dialog(e, self._thread_error_msg, self._thread_exception_type)
        self.job = None


class ImgSaveThread(ThreadBase):
    img_writed = Signal(str)
    _thread_exception_type = "ImgSaveThread"
    _thread_error_msg = QCoreApplication.translate(
        "ImgSaveThread", "Failed to save image.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.im_save_list = []
        self.failure_count = 0

    def saveImg(
        self,
        save_path: str,
        img: QImage,
        pagename_in_proj: str = "",
        save_params: dict = None,
        keep_alpha=False,
    ):
        self.im_save_list.append(
            (save_path, img, pagename_in_proj, save_params, keep_alpha)
        )
        if self.job is None:
            self.job = self._save_img
            self.start()

    def _save_img(self):
        while True:
            if len(self.im_save_list) == 0:
                break
            save_path, img, pagename_in_proj, save_params, keep_alpha = (
                self.im_save_list[0]
            )
            if save_params is None:
                save_params = {}
            if isinstance(img, QImage) or isinstance(img, QPixmap):
                img = pixmap2ndarray(img, keep_alpha=keep_alpha)
            imwrite(save_path, img, **save_params)
            self.img_writed.emit(pagename_in_proj)
            self.im_save_list.pop(0)

    def on_exec_failed(self):
        self.failure_count += 1
        if len(self.im_save_list) > 0:
            self.im_save_list.pop(0)
            if len(self.im_save_list) == 0:
                self.job = None
            else:
                try:
                    self.job()
                except Exception as e:
                    self.on_exec_failed()
                    create_error_dialog(e, self._thread_error_msg, self._thread_exception_type)


class ImgTransProjFileIOThread(ThreadBase):
    fin_page = Signal()
    fin_io = Signal()

    _thread_exception_type = "ImgTransProjFileIOThread"
    _thread_error_msg = QCoreApplication.translate(
        "ImgTransProjFileIOThread", "Thread job failed.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.proj: ProjImgTrans = None
        self.fin_counter = 0
        self.num_pages = 0
        self.fin_page.connect(self.on_fin_page)
        self.progress_bar = ProgressMessageBox("task")

    def on_fin_page(self):
        self.fin_counter += 1
        progress = int(self.fin_counter / self.num_pages * 100)
        self.progress_bar.updateTaskProgress(progress)
        if self.fin_counter == self.num_pages:
            self.progress_bar.hide()

    def on_exec_failed(self):
        self.progress_bar.hide()
