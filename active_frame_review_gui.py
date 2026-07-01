import csv
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageTk, ImageEnhance


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
DISPLAY_BRIGHTNESS_FACTOR = 2.0
LABEL_FOLDERS = [
    "animal",
    "particulate",
    "unsure",
    "discard",
]

# Expected filename:
# 10.0.11.2_202310022249.mp4_frame_00067.jpg
FILENAME_RE = re.compile(
    r"(?P<camera>.+?)_"
    r"(?P<video_datetime>\d{12})\.mp4_"
    r"frame_(?P<frame_number>\d+)"
    r"\.(?P<ext>jpg|jpeg|png|bmp|tif|tiff)$",
    re.IGNORECASE,
)


def parse_image_name(image_name, fps=None):
    """
    Recover camera/date/frame metadata from image name.

    Example:
    10.0.11.2_202310022249.mp4_frame_00067.jpg
    """

    record = {
        "image_name": image_name,
        "parse_ok": False,
        "camera": "",
        "video_datetime": "",
        "video_start": "",
        "frame_number": "",
        "fps": fps if fps is not None else "",
        "frame_timestamp": "",
        "year": "",
        "month": "",
        "day": "",
        "hour": "",
        "minute": "",
    }

    match = FILENAME_RE.match(image_name)

    if not match:
        return record

    camera = match.group("camera")
    video_datetime = match.group("video_datetime")
    frame_number = int(match.group("frame_number"))

    video_start = datetime.strptime(video_datetime, "%Y%m%d%H%M")

    record.update({
        "parse_ok": True,
        "camera": camera,
        "video_datetime": video_datetime,
        "video_start": video_start.isoformat(sep=" "),
        "frame_number": frame_number,
        "year": video_start.year,
        "month": video_start.month,
        "day": video_start.day,
        "hour": video_start.hour,
        "minute": video_start.minute,
    })

    if fps is not None and fps > 0:
        frame_timestamp = video_start + timedelta(seconds=frame_number / fps)
        record["frame_timestamp"] = frame_timestamp.isoformat(sep=" ")

    return record


def get_image_files(folder):
    folder = Path(folder)

    if not folder.exists():
        return []

    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def unique_path(path):
    """
    Prevent accidental overwriting.
    """
    path = Path(path)

    if not path.exists():
        return path

    parent = path.parent
    stem = path.stem
    suffix = path.suffix

    i = 1

    while True:
        candidate = parent / f"{stem}__dup{i}{suffix}"

        if not candidate.exists():
            return candidate

        i += 1


class ActiveFrameReviewGUI:
    def __init__(self, root, batch_dir, fps=None):
        self.root = root
        self.batch_dir = Path(batch_dir)
        self.fps = fps

        self.unreviewed_dir = self.batch_dir / "unreviewed"
        self.label_dirs = {
            label: self.batch_dir / label
            for label in LABEL_FOLDERS
        }

        self.review_log_path = self.batch_dir / "review_log.csv"
        self.current_labels_path = self.batch_dir / "current_labels.csv"

        self.current_image_path = None
        self.current_photo = None
        self.last_move = None

        self.setup_folders()
        self.setup_log()
        self.setup_ui()

        self.load_next_image()

    def setup_folders(self):
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        self.unreviewed_dir.mkdir(parents=True, exist_ok=True)

        for folder in self.label_dirs.values():
            folder.mkdir(parents=True, exist_ok=True)

    def setup_log(self):
        if self.review_log_path.exists():
            return

        with open(self.review_log_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.log_fields())
            writer.writeheader()

    def log_fields(self):
        return [
            "review_time",
            "action",
            "manual_label",
            "source_path",
            "destination_path",
            "image_name",
            "parse_ok",
            "camera",
            "video_datetime",
            "video_start",
            "frame_number",
            "fps",
            "frame_timestamp",
            "year",
            "month",
            "day",
            "hour",
            "minute",
        ]

    def setup_ui(self):
        self.root.title("Active Frame Review")

        self.root.geometry("1300x950")

        self.info_label = tk.Label(
            self.root,
            text="",
            font=("Arial", 12),
            anchor="w",
            justify="left",
        )
        self.info_label.pack(fill="x", padx=10, pady=5)

        self.image_label = tk.Label(self.root)
        self.image_label.pack(expand=True, fill="both", padx=10, pady=10)

        button_frame = tk.Frame(self.root)
        button_frame.pack(fill="x", padx=10, pady=10)

        tk.Button(
            button_frame,
            text="Animal [A]",
            font=("Arial", 14),
            width=18,
            command=lambda: self.classify_current("animal"),
        ).pack(side="left", padx=5)

        tk.Button(
            button_frame,
            text="Particulate [P]",
            font=("Arial", 14),
            width=18,
            command=lambda: self.classify_current("particulate"),
        ).pack(side="left", padx=5)

        tk.Button(
            button_frame,
            text="Unsure [U]",
            font=("Arial", 14),
            width=18,
            command=lambda: self.classify_current("unsure"),
        ).pack(side="left", padx=5)

        tk.Button(
            button_frame,
            text="Discard [D]",
            font=("Arial", 14),
            width=18,
            command=lambda: self.classify_current("discard"),
        ).pack(side="left", padx=5)

        tk.Button(
            button_frame,
            text="Undo [Backspace]",
            font=("Arial", 14),
            width=18,
            command=self.undo_last_move,
        ).pack(side="left", padx=5)

        self.root.bind("a", lambda event: self.classify_current("animal"))
        self.root.bind("A", lambda event: self.classify_current("animal"))

        self.root.bind("p", lambda event: self.classify_current("particulate"))
        self.root.bind("P", lambda event: self.classify_current("particulate"))

        self.root.bind("u", lambda event: self.classify_current("unsure"))
        self.root.bind("U", lambda event: self.classify_current("unsure"))

        self.root.bind("d", lambda event: self.classify_current("discard"))
        self.root.bind("D", lambda event: self.classify_current("discard"))

        self.root.bind("<BackSpace>", lambda event: self.undo_last_move())
        self.root.bind("q", lambda event: self.root.quit())
        self.root.bind("Q", lambda event: self.root.quit())

    def folder_count(self, folder):
        return len(get_image_files(folder))

    def load_next_image(self):
        files = get_image_files(self.unreviewed_dir)

        if len(files) == 0:
            self.current_image_path = None
            self.current_photo = None
            self.image_label.configure(image="", text="No more unreviewed images.")
            self.info_label.configure(text="Done. No more unreviewed images.")
            self.write_current_labels()
            return

        self.current_image_path = files[0]

        image = Image.open(self.current_image_path).convert("RGB")

        # Brighten only for GUI display. The original file is not modified.
        display_image = ImageEnhance.Brightness(image).enhance(DISPLAY_BRIGHTNESS_FACTOR)

        display_image.thumbnail((1250, 780))

        self.current_photo = ImageTk.PhotoImage(display_image)
        self.image_label.configure(image=self.current_photo, text="")

        meta = parse_image_name(self.current_image_path.name, fps=self.fps)

        counts = {
            label: self.folder_count(folder)
            for label, folder in self.label_dirs.items()
        }

        info = (
            f"Current image: {self.current_image_path.name}\n"
            f"Camera: {meta['camera']} | "
            f"Video start: {meta['video_start']} | "
            f"Frame: {meta['frame_number']} | "
            f"Frame timestamp: {meta['frame_timestamp']}\n"
            f"Remaining: {len(files)} | "
            f"Animal: {counts['animal']} | "
            f"Particulate: {counts['particulate']} | "
            f"Unsure: {counts['unsure']} | "
            f"Discard: {counts['discard']}\n"
            f"Keys: A=animal, P=particulate, U=unsure, D=discard, Backspace=undo, Q=quit"
        )

        self.info_label.configure(text=info)

    def append_log(self, action, label, source_path, destination_path):
        source_path = Path(source_path)
        destination_path = Path(destination_path)

        meta = parse_image_name(destination_path.name, fps=self.fps)

        row = {
            "review_time": datetime.now().isoformat(sep=" "),
            "action": action,
            "manual_label": label,
            "source_path": str(source_path),
            "destination_path": str(destination_path),
            **meta,
        }

        with open(self.review_log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.log_fields())
            writer.writerow(row)

    def classify_current(self, label):
        if self.current_image_path is None:
            return

        src = self.current_image_path
        dst = unique_path(self.label_dirs[label] / src.name)

        shutil.move(str(src), str(dst))

        self.last_move = {
            "label": label,
            "from": dst,
            "to": src,
        }

        self.append_log(
            action="classify",
            label=label,
            source_path=src,
            destination_path=dst,
        )

        self.write_current_labels()
        self.load_next_image()

    def undo_last_move(self):
        if self.last_move is None:
            messagebox.showinfo("Undo", "No move to undo.")
            return

        src = Path(self.last_move["from"])
        dst = unique_path(Path(self.last_move["to"]))
        label = self.last_move["label"]

        if not src.exists():
            messagebox.showwarning("Undo", "Could not undo. File not found.")
            self.last_move = None
            return

        shutil.move(str(src), str(dst))

        self.append_log(
            action="undo",
            label=label,
            source_path=src,
            destination_path=dst,
        )

        self.last_move = None

        self.write_current_labels()
        self.load_next_image()

    def write_current_labels(self):
        rows = []

        for label, folder in self.label_dirs.items():
            for image_path in get_image_files(folder):
                meta = parse_image_name(image_path.name, fps=self.fps)

                rows.append({
                    "manual_label": label,
                    "current_path": str(image_path),
                    **meta,
                })

        fieldnames = [
            "manual_label",
            "current_path",
            "image_name",
            "parse_ok",
            "camera",
            "video_datetime",
            "video_start",
            "frame_number",
            "fps",
            "frame_timestamp",
            "year",
            "month",
            "day",
            "hour",
            "minute",
        ]

        with open(self.current_labels_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def run_gui(batch_dir, fps=None):
    root = tk.Tk()
    app = ActiveFrameReviewGUI(root, batch_dir=batch_dir, fps=fps)
    root.mainloop()


if __name__ == "__main__":
    # Edit this path:
    BATCH_DIR = r"active_review_batch_003"

    # Edit this if needed:
    FPS = 26

    run_gui(
        batch_dir=BATCH_DIR,
        fps=FPS,
    )