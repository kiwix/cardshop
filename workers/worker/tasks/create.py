#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu

import os
import json
import logging
import subprocess

from urllib.parse import urlparse

from tasks.base import BaseTask
from utils.setting import Setting
from utils import get_checksum
from utils.scheduler import authenticate, get_access_token

logger = logging.getLogger(__name__)


def free_space():
    """ clean up cache folder """
    logger.info("clean-up cache folder to free space")
    clean_cache_output = subprocess.run(
        args=[
            Setting.installer_binary_path,
            "cache",
            "--build",
            Setting.working_dir,
            "reset",
        ],
        universal_newlines=True,
        capture_output=True,
        check=True,
    ).stdout
    logger.info(clean_cache_output)


class CreateTask(BaseTask):
    def remove_files(self, only_errorneous=True):
        suffixes = [".ERROR.img", ".BUILDING.img"]
        if not only_errorneous:
            suffixes.append(".img")

        for suffix in suffixes:
            path = Setting.working_dir.joinpath(self.img_name + suffix)
            if path.is_file():
                logger.info("removing {}".format(path.name))
                path.unlink()

        # delete config and log
        for path in (self.log_path, self.config_path):
            if path.is_file():
                path.unlink()

    @property
    def img_path(self):
        return self.file_path("img")

    @property
    def img_name(self):
        return os.path.splitext(self.img_path.name)[0]

    @property
    def log_path(self):
        return self.file_path("txt")

    @property
    def uploader_log_path(self):
        return self.file_path("log")

    @property
    def config_path(self):
        return self.file_path("json")

    def run(self):
        super().run()

        # make sure warehouse URI is OK before we start
        try:
            urlparse(self.task["upload_uri"])
        except Exception as exp:
            self.logger.error("Error: Warehouse URI could not be parsed")
            self.logger.exception(exp)
            self.exception = exp
            self.report_status("failed", str(exp))
            return

        states = [
            ("build_image", "building", "built", "failed_to_build"),
            ("upload_image", "uploading", "uploaded", "failed_to_upload"),
        ]

        for method, working_status, success_status, failed_status in states:
            try:
                self.report_status(working_status)
                getattr(self, method)()
            except Exception as exp:
                self.logger.exception(exp)
                self.exception = exp
                if self.canceled:
                    self.report_status("canceled")
                else:
                    self.report_status(failed_status, str(exp))
                return
            else:
                self.report_status(success_status)

    def build_image(self):
        # clean up cache
        free_space()

        self.logger.info("Starting image creation")

        # write config to a file
        with open(str(self.config_path), "w") as fd:
            json.dump(self.task.get("config"), fd, indent=4)

        args = [
            str(Setting.installer_binary_path),
            "cli",
            "--root",
            "--filename",
            self.img_name,
            "--build-dir",
            str(Setting.working_dir),
            "--config",
            str(self.config_path),
            "--size",
            "{}GB".format(self.task.get("size")),
        ]

        if bool(os.getenv("FAKE_CREATION", False)):
            args = ["cp", str(Setting.installer_binary_path), str(self.img_path)]
        self.logger.info("Starting {args}\n".format(args=" ".join(args)))

        self.logs["installer_log"] = "{args}\n".format(args=" ".join(args))
        log_fd = open(str(self.log_path), "w")
        ps = subprocess.Popen(
            args=args,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            close_fds=True,
        )

        self.logger.info("installer started: {}".format(ps))
        while ps.poll() is None:
            self.logs["installer_log"] = self.read_log(self.log_path)

            # kill upon request
            if self.canceled:
                self.logger.info("terminating installer…")
                ps.terminate()
                ps.wait(10)
                ps.kill()
                ps.wait(10)
                break

        ps.wait(10)

        if ps.returncode == 0:
            self.logger.info("installer ran successfuly.")
            # get and store size and checksum
            self.extra["image"] = {
                "fname": self.img_path.name,
                "size": self.img_path.stat().st_size,
                "checksum": "{0}:{1}".format(*get_checksum(str(self.img_path))),
            }
        else:
            self.logger.error("installer failed: {}".format(ps.returncode))

        self.logger.info("collecting full terminated log")
        self.logs["installer_log"] = self.read_log(self.log_path)

        # clean up working folder
        self.remove_files()

        if ps.returncode != 0:
            raise subprocess.SubprocessError("installer rc: {}".format(ps.returncode))

    def upload_image(self):
        self.logger.info("Starting upload")

        self.logger.info("re-authenticate to ensure token is still valid")
        authenticate(force=True)

        url = self.task["upload_uri"]

        args = [
            str(Setting.curl_binary_path),
            "--append",
            "--connect-timeout",
            "60",
            "--continue-at",
            "-",
            "--insecure",
            "--ipv4",
            "--retry-connrefused",
            "--retry-delay",
            "60",
            "--retry",
            "20",
            "--stderr",
            "-",
            "--user",
            "{user}:{passwd}".format(user=Setting.username, passwd=get_access_token()),
            "--upload-file",
            str(self.img_path),
            url,
        ]

        if Setting.proxy:
            args = args[0:1] + ["--proxy", Setting.proxy] + args[1:]

        log_args = args[:-4] + ["{}:xxxxx".format(Setting.username)] + args[-3:]
        self.logger.info("Starting {args}\n".format(args=" ".join(log_args)))

        self.logs["uploader_log"] = "{args}\n".format(args=" ".join(log_args))
        log_fd = open(str(self.uploader_log_path), "w")
        ps = subprocess.Popen(
            args=args,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            close_fds=True,
        )

        self.logger.info("uploader started: {}".format(ps))
        while ps.poll() is None:
            self.logs["uploader_log"] = self.read_log(self.uploader_log_path)

            # kill upon request
            if self.canceled:
                self.logger.info("terminating uploader…")
                ps.terminate()
                ps.wait(10)
                ps.kill()
                ps.wait(10)
                break

        ps.wait(10)

        if ps.returncode == 0:
            self.logger.info("uploader ran successfuly.")
        else:
            self.logger.error("uploader failed: {}".format(ps.returncode))

        self.logger.info("collecting full terminated log")
        self.logs["uploader_log"] = self.read_log(self.uploader_log_path)

        # remove image
        try:
            self.logger.info("removing image file: {}".format(self.img_path.name))
            self.img_path.unlink()
        except Exception as exp:
            self.logger.error("Unable to remove image file: {}".format(exp))
            self.logger.exception(exp)

        if ps.returncode != 0:
            raise subprocess.SubprocessError("uploader rc: {}".format(ps.returncode))
