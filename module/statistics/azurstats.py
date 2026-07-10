import threading
import os
import sqlite3
import time
import uuid
from datetime import datetime
from dataclasses import asdict

import numpy as np

from module.base.utils import save_image
from module.logger import logger
from module.statistics.utils import pack
from module.base.device_id import get_device_id


class DropImage:
    def __init__(self, stat, genre, save, local, info=''):
        """
        Args:
            stat (AzurStats):
            genre:
            save:
            local:
        """
        self.stat = stat
        self.genre = str(genre)
        self.save = bool(save)
        self.local = bool(local)
        self.info = info
        self.images = []
        self.combat_count = 0

    def add(self, image):
        """
        Args:
            image (np.ndarray):
        """
        if self:
            self.images.append(image)
            logger.info(
                f'Drop record added, genre={self.genre}, amount={self.count}')

    def set_combat_count(self, count):
        self.combat_count = count

    def handle_add(self, main, before=None):
        """
        Handle wait before and after adding screenshot.

        Args:
            main (ModuleBase):
            before (int, float, tuple): Sleep before adding.
        """
        if before is None:
            before = main.config.WAIT_BEFORE_SAVING_SCREEN_SHOT

        if self:
            main.handle_info_bar()
            main.device.sleep(before)
            main.device.screenshot()
            self.add(main.device.image)

    def clear(self):
        self.images = []

    @property
    def count(self):
        return len(self.images)

    def __bool__(self):
        return self.save or self.local

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self:
            self.stat.commit(images=self.images, genre=self.genre,
                             save=self.save, local=self.local, info=self.info, combat_count=self.combat_count)


class AzurStats:
    TIMEOUT = 20
    LOCAL_DB = './config/azurstats_local.db'
    LOCAL_MEOW_CSV = './log/azurstat_meowofficer_farming.csv'
    LOCAL_GENRES = {'opsi_meowfficer_farming'}
    _local_lock = threading.Lock()
    _record_lock = threading.Lock()

    def __init__(self, config):
        """
        Args:
            config:
        """
        self.config = config

    meowofficer_farming_labels = ['侵蚀等级', '上次记录时间', '有效战斗轮数', '平均黄币/轮', '平均金菜/轮', '平均深渊/轮', '平均隐秘/轮']
    meowofficer_farming_map = [
        'OperationCoin',
        'Plate',
        'CoordinateAbyssal',
        'CoordinateObscure'
    ]
    unit_combat_count = {
        1: 2,
        2: 2,
        3: 2,
        4: 3,
        5: 3,
        6: 3
    }

    @staticmethod
    def load_meowofficer_farming():
        """
        Returns:
            np.ndarray: Stats.
        """
        try:
            data = np.loadtxt(AzurStats.LOCAL_MEOW_CSV, delimiter=',', dtype=float, skiprows=1, encoding='utf-8')
            if data.shape[0] != 6:
                raise IndexError
        except Exception:
            data = np.zeros((6, len(AzurStats.meowofficer_farming_labels)))
            data[:, 0] = np.arange(1, 7)
            header = ','.join(AzurStats.meowofficer_farming_labels)
            os.makedirs(os.path.dirname(AzurStats.LOCAL_MEOW_CSV), exist_ok=True)
            np.savetxt(AzurStats.LOCAL_MEOW_CSV, data, delimiter=',', header=header, comments='', fmt='%f', encoding='utf-8')
            data = np.loadtxt(AzurStats.LOCAL_MEOW_CSV, delimiter=',', dtype=float, skiprows=1, encoding='utf-8')
        return data

    @staticmethod
    def _ensure_local_db():
        os.makedirs(os.path.dirname(AzurStats.LOCAL_DB), exist_ok=True)
        with sqlite3.connect(AzurStats.LOCAL_DB) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS opsi_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    imgid TEXT NOT NULL,
                    server TEXT,
                    zone TEXT,
                    zone_type TEXT,
                    zone_id INTEGER,
                    hazard_level INTEGER,
                    item TEXT,
                    amount INTEGER,
                    tag TEXT,
                    device_id TEXT,
                    genre TEXT,
                    combat_count INTEGER,
                    created_at INTEGER
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_opsi_items_device_genre ON opsi_items(device_id, genre)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_opsi_items_imgid ON opsi_items(imgid)')
            conn.commit()

    @staticmethod
    def _insert_local_opsi_items(rows):
        if not rows:
            return 0

        AzurStats._ensure_local_db()
        with AzurStats._local_lock:
            with sqlite3.connect(AzurStats.LOCAL_DB) as conn:
                conn.executemany('''
                    INSERT INTO opsi_items (
                        imgid, server, zone, zone_type, zone_id, hazard_level,
                        item, amount, tag, device_id, genre, combat_count, created_at
                    ) VALUES (
                        :imgid, :server, :zone, :zone_type, :zone_id, :hazard_level,
                        :item, :amount, :tag, :device_id, :genre, :combat_count, :created_at
                    )
                ''', rows)
                conn.commit()
        return len(rows)

    @staticmethod
    def _load_local_opsi_items(device_id=None, genre='opsi_meowfficer_farming'):
        AzurStats._ensure_local_db()
        query = 'SELECT * FROM opsi_items WHERE 1=1'
        params = []
        if device_id:
            query += ' AND device_id = ?'
            params.append(device_id)
        if genre:
            query += ' AND genre = ?'
            params.append(genre)
        query += ' ORDER BY id ASC'

        with sqlite3.connect(AzurStats.LOCAL_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _write_meowofficer_farming(data):
        header = ','.join(AzurStats.meowofficer_farming_labels)
        os.makedirs(os.path.dirname(AzurStats.LOCAL_MEOW_CSV), exist_ok=True)
        np.savetxt(
            AzurStats.LOCAL_MEOW_CSV,
            data,
            delimiter=',',
            header=header,
            comments='',
            fmt='%f',
            encoding='utf-8',
        )

    @staticmethod
    def get_meowofficer_farming():
        all_data = AzurStats._load_local_opsi_items(
            device_id=get_device_id(),
            genre='opsi_meowfficer_farming',
        )
        out_data = np.zeros((6, len(AzurStats.meowofficer_farming_labels)))
        img_combat_counts = {}

        for row in all_data:
            imgid = row.get('imgid')
            h_level = row.get('hazard_level')
            if not h_level or h_level < 1 or h_level > 6:
                continue
                
            combat_count = row.get('combat_count', 0)
            if imgid not in img_combat_counts:
                img_combat_counts[imgid] = combat_count
                out_data[h_level - 1, 2] += combat_count
            
            item_name = row.get('item')
            amount = row.get('amount', 0)
            
            for i, item_prefix in enumerate(AzurStats.meowofficer_farming_map):
                if item_name.startswith(item_prefix):
                    out_data[h_level - 1, 3 + i] += amount
                    break
        current_time = int(datetime.timestamp(datetime.now()))

        for i in range(6):
            h = i + 1
            out_data[i, 0] = h
            out_data[i, 1] = current_time
            out_data[i, 2] /= AzurStats.unit_combat_count[h]

            if out_data[i, 2] > 0:
                for j in range(3, len(AzurStats.meowofficer_farming_labels)):
                    out_data[i, j] /= out_data[i, 2]

        AzurStats._write_meowofficer_farming(out_data)
        logger.info('[Statistics] 本地统计数据更新成功: azurstat_meowofficer_farming.csv')

    @staticmethod
    def _ensure_local_parser():
        from module.azur_stats.scene.operation_siren import SceneOperationSiren
        return SceneOperationSiren

    @staticmethod
    def _parse_local_opsi_items(image, imgid, genre, combat_count):
        SceneOperationSiren = AzurStats._ensure_local_parser()
        scene = SceneOperationSiren()
        scene.load_file(image)
        scene.__dict__['imgid'] = imgid
        rows = []
        created_at = int(time.time())
        device_id = get_device_id()

        for item in scene.parse_scene():
            row = asdict(item)
            row['imgid'] = imgid
            row['device_id'] = device_id
            row['genre'] = genre
            row['combat_count'] = int(combat_count or 0)
            row['created_at'] = created_at
            rows.append(row)

        return rows

    def _record_local(self, image, genre, filename, combat_count):
        if genre not in ['opsi_meowfficer_farming']:
            return False

        imgid = f"{os.path.splitext(os.path.basename(filename))[0][:8]}{uuid.uuid4().hex[:8]}"
        try:
            rows = self._parse_local_opsi_items(image, imgid, genre, combat_count)
            if not rows:
                logger.warning('Local AzurStats parse skipped, no opsi item rows extracted')
                return False
            inserted = self._insert_local_opsi_items(rows)
            self.get_meowofficer_farming()
            logger.info(f'Local AzurStats parse success, rows={inserted}')
            return True
        except Exception as e:
            logger.warning(f'Local AzurStats parse failed, {e}')
            return False

    def _save(self, image, genre, filename):
        """
        Args:
            image: Image to save.
            genre (str): Name of sub folder.
            filename (str): 'xxx.png'

        Returns:
            bool: If success
        """
        try:
            folder = os.path.join(
                str(self.config.DropRecord_SaveFolder), genre)
            os.makedirs(folder, exist_ok=True)
            file = os.path.join(folder, filename)
            save_image(image, file)
            logger.info(f'Image save success, file: {file}')
            return True
        except Exception as e:
            logger.exception(e)

        return False

    def commit(self, images, genre, save=False, local=False, info='', combat_count=0):
        """
        Args:
            images (list): List of images in numpy array.
            genre (str):
            save (bool): If save image to local file system.
            local (bool): If parse image into local AzurStats storage.
            info (str): Extra info append to filename.

        Returns:
            bool: If commit.
        """
        if len(images) == 0:
            return False

        save, local = bool(save), bool(local)
        logger.info(
            f'Drop record commit, genre={genre}, amount={len(images)}, save={save}, local={local}')
        image = pack(images)
        now = int(time.time() * 1000)

        if info:
            filename = f'{now}_{info}.png'
        else:
            filename = f'{now}.png'

        if save:
            save_thread = threading.Thread(
                target=self._save, args=(image, genre, filename))
            save_thread.start()

        if local:
            logger.info(f'Local AzurStats parse start, genre={genre}')
            with self._record_lock:
                self._record_local(image, genre, filename, combat_count)

        return True

    def new(self, genre, method=None, save=False, local=None, info=''):
        """
        Args:
            genre (str):
            method (str): The method about save and upload image.
            save (bool): Whether to save the image.
            local (bool): Whether to use local processing. If None, determined by genre.
            info (str): Extra info append to filename.

        Returns:
            DropImage:
        """
        method_value = None
        if isinstance(method, bool):
            save = save or method
            method = None
        if method is not None:
            method_value = str(method)
            save = save or 'save' in method_value
        if local is None:
            if method_value is None:
                local = genre in self.LOCAL_GENRES
            else:
                local = 'upload' in method_value and genre in self.LOCAL_GENRES
        return DropImage(stat=self, genre=genre, save=save, local=local, info=info)
