#!/bin/bash
is_binary_file() {
  local file="$1"
  if file -b --mime-encoding "$file" | grep -q -v '^us-ascii$'; then
    return 0
  else
    return 1
  fi
}
virtualenv_dir=""

find_virtualenv() {
  local current_dir="$1"
  local virtualenv_dir=""

  while IFS= read -r dir; do

    if [[ -d "$dir/bin" && -f "$dir/pyvenv.cfg" ]]; then
      python_file="$dir/bin/python"
      if [[ -f "$python_file" ]] && is_binary_file "$python_file"; then
        virtualenv_dir="$(basename "$dir")"
        break
      fi
      # Check if "python.exe" file is present and a binary
      python_exe_file="$dir/Scripts/python.exe"
      if [[ -f "$python_exe_file" ]] && is_binary_file "$python_exe_file"; then
        virtualenv_dir="$(basename "$dir")"
        break
      fi
    fi
  done < <(find "$current_dir" -maxdepth 1 -type d)
  echo $virtualenv_dir
}

current_directory=$(pwd)

virtualenv_dir=`find_virtualenv "$current_directory" &`
wait $!
echo
if [[ -n "$virtualenv_dir" ]]; then
  echo "Virtual environment directory found: $virtualenv_dir"
else
  echo "No virtual environment directory found in the current directory."
fi
echo "$virtualenv_dir" > variables



echo "creating json file with details to update in db...."
if test "$STATUS"
then 
    echo "{\"repo_name\":\"${REPO_NAME}\",\"status\":\"${STATUS}\",\"run_id\":\"${RUN_ID}\",\"service_name\":\"${SERVICE_NAME}\"}" > $REPO_NAME.json
else
    echo "{\"repo_name\":\"${REPO_NAME}\",\"service_name\":\"${SERVICE_NAME}\",\"deployed_url\":\"${DEPLOYED_URL}\",\"s3_path\":\"${S3_PATH}\",\"run_id\":\"${RUN_ID}\"}" > $REPO_NAME.json
fi
# echo "{\"repo_name\":\"${REPO_NAME}\",\"service_name\":\"${SERVICE_NAME}\",\"deployed_url\":\"${DEPLOYED_URL}\",\"s3_path\":\"${S3_PATH}\"}" > $REPO_NAME.json
echo "setting aws config .........."
aws configure set default.region us-east-2
aws configure set aws_access_key_id $AWS_ACCESS_KEY
aws configure set aws_secret_access_key $AWS_SECRET_KEY
echo "copying file to aws.........."
aws s3 sync . "s3://dna-learningndev-prod-bucket/github-apiupdate/" --exclude '*' --include="$REPO_NAME.json"
echo "Note this step supress exit code of aws sync step. for debugging use normal ways to test the flow. check main.yml for S3-upload"