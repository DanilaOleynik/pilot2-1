#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Pavlo Svirin, pavlo.svirin@cern.ch, 2017
# - Tobias Wegner, tobias.wegner@cern.ch, 2018
# - Paul Nilsson, paul.nilsson@cern.ch, 2018
# - Alexey Anisenkov, anisyonk@cern.ch, 2018

import os
import logging
import errno

from .common import resolve_common_transfer_errors
from pilot.common.exception import PilotException, ErrorCodes, StageInFailure, StageOutFailure
from pilot.util.container import execute

logger = logging.getLogger(__name__)

require_replicas = True  ## indicate if given copytool requires input replicas to be resolved

allowed_schemas = ['srm', 'gsiftp', 'https', 'davs']  # prioritized list of supported schemas for transfers by given copytool


def is_valid_for_copy_in(files):
    return True  ## FIX ME LATER
    #for f in files:
    #    if not all(key in f for key in ('name', 'source', 'destination')):
    #        return False
    #return True


def is_valid_for_copy_out(files):
    return True  ## FIX ME LATER
    #for f in files:
    #    if not all(key in f for key in ('name', 'source', 'destination')):
    #        return False
    #return True


def get_timeout(filesize):   ## ISOLATE ME LATER
    """ Get a proper time-out limit based on the file size """

    timeout_max = 3 * 3600  # 3 hours
    timeout_min = 300  # self.timeout

    timeout = timeout_min + int(filesize / 0.5e6)  # approx < 0.5 Mb/sec

    return min(timeout, timeout_max)


def copy_in(files, **kwargs):
    """
        Download given files using gfal-copy command.

        :param files: list of `FileSpec` objects
        :raise: PilotException in case of controlled error
    """

    allow_direct_access = kwargs.get('allow_direct_access') or False

    if not check_for_gfal():
        raise StageInFailure("No GFAL2 tools found")

    for fspec in files:
        # continue loop for files that are to be accessed directly
        if fspec.is_directaccess(ensure_replica=False) and allow_direct_access:
            fspec.status_code = 0
            fspec.status = 'remote_io'
            continue

        dst = fspec.workdir or kwargs.get('workdir') or '.'

        timeout = get_timeout(fspec.filesize)
        source = fspec.turl
        destination = "file://%s" % os.path.abspath(os.path.join(dst, fspec.lfn))

        cmd = ['gfal-copy --verbose -f', ' -t %s' % timeout]

        if fspec.checksum:
            cmd += ['-K', '%s:%s' % fspec.checksum.items()[0]]

        cmd += [source, destination]

        rcode, stdout, stderr = execute(" ".join(cmd), **kwargs)

        if rcode:  ## error occurred
            if rcode in [errno.ETIMEDOUT, errno.ETIME]:
                error = {'rcode': ErrorCodes.STAGEINTIMEOUT,
                         'state': 'CP_TIMEOUT',
                         'error': 'Copy command timed out: %s' % stderr}
            else:
                error = resolve_common_transfer_errors(stdout + stderr, is_stagein=True)
            fspec.status = 'failed'
            fspec.status_code = error.get('rcode')
            raise PilotException(error.get('error'), code=error.get('rcode'), state=error.get('state'))

        fspec.status_code = 0
        fspec.status = 'transferred'

    return files


def copy_out(files, **kwargs):
    """
    Upload given files using gfal command.

    :param files: Files to upload
    :raises: PilotException in case of errors
    """

    if not check_for_gfal():
        raise StageOutFailure("No GFAL2 tools found")

    for fspec in files:

        src = fspec.workdir or kwargs.get('workdir') or '.'

        timeout = get_timeout(fspec.filesize)

        source = "file://%s" % os.path.abspath(fspec.surl or os.path.join(src, fspec.lfn))
        destination = fspec.turl

        cmd = ['gfal-copy --verbose -f', ' -t %s' % timeout]

        if fspec.checksum:
            cmd += ['-K', '%s:%s' % fspec.checksum.items()[0]]

        cmd += [source, destination]

        rcode, stdout, stderr = execute(" ".join(cmd), **kwargs)

        if rcode:  ## error occurred
            if rcode in [errno.ETIMEDOUT, errno.ETIME]:
                error = {'rcode': ErrorCodes.STAGEOUTTIMEOUT,
                         'state': 'CP_TIMEOUT',
                         'error': 'Copy command timed out: %s' % stderr}
            else:
                error = resolve_common_transfer_errors(stdout + stderr, is_stagein=False)
            fspec.status = 'failed'
            fspec.status_code = error.get('rcode')
            raise PilotException(error.get('error'), code=error.get('rcode'), state=error.get('state'))

        fspec.status_code = 0
        fspec.status = 'transferred'

    return files


def move_all_files_in(files, nretries=1):   ### NOT USED -- TO BE DEPRECATED
    """
    Move all files.

    :param files:
    :param nretries: number of retries; sometimes there can be a timeout copying, but the next attempt may succeed
    :return: exit_code, stdout, stderr
    """

    exit_code = 0
    stdout = ""
    stderr = ""

    for entry in files:  # entry = {'name':<filename>, 'source':<dir>, 'destination':<dir>}
        logger.info("transferring file %s from %s to %s" % (entry['name'], entry['source'], entry['destination']))

        source = entry['source'] + '/' + entry['name']
        # why /*4 ? Because sometimes gfal-copy complains about file:// protocol (anyone knows why?)
        # with four //// this does not seem to happen
        destination = 'file:///' + os.path.join(entry['destination'], entry['name'])
        for retry in range(nretries):
            exit_code, stdout, stderr = move(source, destination, entry.get('recursive', False))

            if exit_code != 0:
                if ((exit_code != errno.ETIMEDOUT) and (exit_code != errno.ETIME)) or (retry + 1) == nretries:
                    logger.warning("transfer failed: exit code = %d, stdout = %s, stderr = %s" % (exit_code, stdout, stderr))
                    return exit_code, stdout, stderr
            else:  # all successful
                break

    return exit_code, stdout, stderr


def move_all_files_out(files, nretries=1):  ### NOT USED -- TO BE DEPRECATED
    """
    Move all files.

    :param files:
    :return: exit_code, stdout, stderr
    """

    exit_code = 0
    stdout = ""
    stderr = ""

    for entry in files:  # entry = {'name':<filename>, 'source':<dir>, 'destination':<dir>}
        logger.info("transferring file %s from %s to %s" % (entry['name'], entry['source'], entry['destination']))

        destination = entry['destination'] + '/' + entry['name']
        # why /*4 ? Because sometimes gfal-copy complains about file:// protocol (anyone knows why?)
        # with four //// this does not seem to happen
        source = 'file:///' + os.path.join(entry['source'], entry['name'])
        for retry in range(nretries):
            exit_code, stdout, stderr = move(source, destination)

            if exit_code != 0:
                if ((exit_code != errno.ETIMEDOUT) and (exit_code != errno.ETIME)) or (retry + 1) == nretries:
                    logger.warning("transfer failed: exit code = %d, stdout = %s, stderr = %s" % (exit_code, stdout, stderr))
                    return exit_code, stdout, stderr
            else:  # all successful
                break

    return exit_code, stdout, stderr


def move(source, destination, recursive=False):
    cmd = None
    if recursive:
        cmd = "gfal-copy -r %s %s" % (source, destination)
    else:
        cmd = "gfal-copy %s %s" % (source, destination)
    print(cmd)
    exit_code, stdout, stderr = execute(cmd)

    return exit_code, stdout, stderr


def check_for_gfal():
    exit_code, gfal_path, _ = execute('which gfal-copy')
    return exit_code == 0
