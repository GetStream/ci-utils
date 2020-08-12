import argparse
import base64
import subprocess
import re
import sys
import urllib.request
import json
from datetime import date


class Jira(object):
    def __init__(self, project_key, email, api_key, jira_url):
        self.project_key = project_key
        self.email = email
        self.api_key = api_key
        self.project = None
        self.jira_url = jira_url

    def add_jira_auth(self, req):
        encoded_credentials = base64.b64encode(
            f"{self.email}:{self.api_key}".encode("ascii")
        )
        req.add_header("Authorization", f'Basic {encoded_credentials.decode("ascii")}')
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")

    def load_project(self):
        req = urllib.request.Request(
            f"{self.jira_url}/rest/api/2/project/{self.project_key}"
        )
        self.add_jira_auth(req)

        with urllib.request.urlopen(req) as response:
            self.project = json.loads(response.read())

        return self.project

    def add_fix_version_to_issue(self, issue_id, version):
        print(f"mark issue {issue_id} fixed by this version")

        payload = json.dumps(
            {"update": {"fixVersions": [{"add": {"name": version}}]}}
        ).encode("utf-8")

        req = urllib.request.Request(
            f"{self.jira_url}/rest/api/2/issue/{issue_id}", payload, method="PUT",
        )

        self.add_jira_auth(req)

        with urllib.request.urlopen(req) as response:
            response.read()

    def assert_version(self, version):
        print(f"get or create version {version} for {self.project_key}")

        payload = json.dumps(
            {
                "archived": False,
                "releaseDate": date.today().isoformat(),
                "name": version,
                "projectId": self.project["id"],
                "released": True,
            }
        ).encode("utf-8")
        req = urllib.request.Request(f"{self.jira_url}/rest/api/2/version", payload)
        self.add_jira_auth(req)

        try:
            with urllib.request.urlopen(req) as response:
                json.loads(response.read())
        except urllib.error.HTTPError as e:
            response = json.loads(e.read().decode("utf8"))
            if (
                response.get("errors", {}).get("name")
                == "A version with this name already exists in this project."
            ):
                print("version is already on Jira, moving on")


class Github(object):
    def __init__(self, repo_owner, repo_name, token):
        self.token = token
        self.repo_owner = repo_owner
        self.repo_name = repo_name

    def get_jira_id_from_pr(self, jira_project_key, pr_id):
        encoded_credentials = base64.b64encode(f"{self.token}:".encode("ascii"))
        url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/pulls/{pr_id}"

        req = urllib.request.Request(url)
        req.add_header(
            "Authorization", "Basic %s" % encoded_credentials.decode("ascii")
        )

        jira_issue_ids = []

        with urllib.request.urlopen(req) as response:
            body = response.read()
            data = json.loads(body)
            jira_issue_ids += re.findall(f"{jira_project_key}-[\d]+", data["title"])
            jira_issue_ids += re.findall(
                f"{jira_project_key}-[\d]+", data["head"]["ref"]
            )

        return jira_issue_ids


def find_previous_version_tag(version):
    output = subprocess.check_output(
        ["git", "tag", "--sort", "-v:refname", "-l", "v[0-9]*"]
    ).strip()
    tags = [t.decode("utf-8") for t in output.split(b"\n")]

    # exclude version like this one v1.0.0-beta
    tags = [t for t in tags if "-" not in t]

    if version not in tags:
        print(f"{version} not found in current list of tags")
        return None

    i = tags.index(version)

    if len(tags) <= i:
        return None

    return tags[i + 1]


def list_merged_prs(prev_version, version):
    print(f"listing all merged PRs between {prev_version} and {version}")
    output = subprocess.check_output(
        ["git", "log", f"{prev_version}...{version}", "--grep", "#", "--grep", "]"]
    ).strip()
    logs = output.decode("utf-8")
    return [t.split("#")[1] for t in re.findall(r"#[\d]+", logs)]


def main():
    parser = argparse.ArgumentParser(description="Jira Release Script")
    parser.add_argument("--jira-project-key", required=True)
    parser.add_argument("--github-token", required=True)
    parser.add_argument("--github-repo-owner", required=True)
    parser.add_argument("--github-repo-name", required=True)
    parser.add_argument("--jira-email", required=True)
    parser.add_argument("--jira-api-key", required=True)
    parser.add_argument("--jira-url", required=True)
    parser.add_argument("--version")

    args = parser.parse_args()

    try:
        print("git fetch --unshallow")
        out = subprocess.check_output(["git", "fetch", "--unshallow"])
        print(out.decode("utf-8"))
    except subprocess.CalledProcessError:
        pass

    try:
        print("git fetch --tags")
        out = subprocess.check_output(["git", "fetch", "--tags"])
        print(out.decode("utf-8"))
    except subprocess.CalledProcessError:
        pass

    version = args.version
    if not version:
        version = (
            subprocess.check_output(["git", "describe", "--tags"])
            .strip()
            .decode("utf-8")
        )
    if not re.match("^v\d+\.\d+\.\d+$", version):
        return

    github = Github(args.github_repo_owner, args.github_repo_name, args.github_token)
    jira = Jira(
        args.jira_project_key, args.jira_email, args.jira_api_key, args.jira_url
    )

    jira.load_project()

    prev_version = find_previous_version_tag(version)
    if prev_version is None:
        print(f"did not find a version before {version}, fall back to last 100 commits")
        prev_version = f"{version}~100"

    merged_prs = list_merged_prs(prev_version, version)

    print(f"Merged Pull Requests: {', '.join(merged_prs)}")

    jira_issue_ids = []
    for pr_id in merged_prs:
        jira_issue_ids += github.get_jira_id_from_pr(args.jira_project_key, pr_id,)

    jira_issue_ids = list(set(jira_issue_ids))
    print(f"found these Jira IDs for this release {jira_issue_ids}")

    jira.assert_version(version)

    for issue_id in jira_issue_ids:
        jira.add_fix_version_to_issue(issue_id, version)


if __name__ == "__main__":
    main()
