# -*- coding: utf-8 -*-

import typing as T
import subprocess
from datetime import datetime, timezone

from boto_session_manager import BotoSesManager
from s3pathlib import S3Path, context

# ------------------------------------------------------------------------------
# update this code block to customize the configuration

# where you want to store your backup?
backup_bucket = "bmt-app-dev-us-east-1-important-backup"

# This should match the definition in the backup S3 bucket policy and
# the CodeBuild IAM role. The last aws_account_id is the AWS account ID
# where the CodeCommit repo is located.
backup_folder = "projects/codecommit-backup/807388292768"

# list of repos you want to back up.

# TODO: currently it only support explicit enumeration, will add regex support
repo_list = [
    "codecommit-backup",
]

# you keep at least last N backup for each repo
keep_at_least = 3

# automatically delete backup older than N days, only if there are more than "keep_at_least" backups
retention_period = 30  # days
# ------------------------------------------------------------------------------


def get_repo_list(
    bsm: BotoSesManager,
    repo_list: T.List[str],
) -> T.List[str]:
    """
    This method determines the list of repos to backup.

    TODO: currently it only support explicit enumeration, will add regex support
    """
    if len(repo_list) == 0:
        paginator = bsm.codecommit_client.get_paginator("list_repositories")
        repo_list = list()
        for res in paginator.paginate(
            PaginationConfig={"MaxItems": 1000},
        ):
            for dct in res.get("repositories", []):
                repo_list.append(dct["repositoryName"])
        return repo_list
    else:
        return repo_list


def backup_one_repo(
    bsm: BotoSesManager,
    repo_name: str,
):
    # clone repo
    repo_arn = f"arn:aws:codecommit:{bsm.aws_region}:{bsm.aws_account_id}:{repo_name}"
    print(f"clone repo {repo_arn}")
    args = [
        "git",
        "clone",
        "-q",
        f"codecommit::{bsm.aws_region}://{repo_name}",
        f"{repo_name}",
    ]
    subprocess.run(args, capture_output=True, check=True)

    # zip repo
    args = ["zip", "-yr", f"{repo_name}.zip", f"./{repo_name}"]
    subprocess.run(args, check=True)

    # upload repo
    time_str = datetime.utcnow().strftime("%Y-%m-%d-%H-%M-%S")
    s3_uri = f"s3://{backup_bucket}/{backup_folder}/{bsm.aws_region}/{repo_name}/{repo_name}-{time_str}.zip"
    s3path = S3Path.from_s3_uri(s3_uri)
    s3path.upload_file(f"{repo_name}.zip", overwrite=False)

    tags = {
        "tech:project_name": "codecommit-backup",
        "tech:source_repo_arn": repo_arn,
    }
    s3path.put_tags(tags=tags)

    print(f"preview backup of {repo_name!r} at: {s3path.console_url}")

    # clean up old backups
    s3dir = s3path.parent
    s3path_list = sorted(
        s3dir.iter_objects(),
        key=lambda s3path: s3path.last_modified_at,
        reverse=True,
    )

    if len(s3path_list) > keep_at_least:
        s3path: S3Path
        for s3path in s3path_list[keep_at_least:]:
            if (now - s3path.last_modified_at).total_seconds() >= (
                retention_period * 24 * 60 * 60
            ):
                s3path.delete_if_exists()


if __name__ == "__main__":
    bsm = BotoSesManager()
    context.attach_boto_session(bsm.boto_ses)
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    for repo_name in get_repo_list(bsm=bsm, repo_list=repo_list):
        print(f"try to backup {repo_name!r}")
        try:
            backup_one_repo(bsm=bsm, repo_name=repo_name)
            print("  succeeded!")
        except Exception as e:
            print(f"  failed!")
