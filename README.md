aws-backup-lambda
=================

[![Build Status](https://travis-ci.org/cevoaustralia/aws-backup-lambda.svg?branch=master)](https://travis-ci.org/cevoaustralia/aws-backup-lambda)

A utility AWS lambda function to manage EBS and RDS snapshot backups.

 The Lambda function takes new backups when executed, and manages the deletion of the old ones when the upper limit is reached.

The origin of this project comes from: https://github.com/evannuil/aws-snapshot-tool

## Dependencies

The tool uses the supplied `boto3` library to connect to the AWS account, and uses the IAM Role defined in the CloudFormation stack to enable access to the required assets.

## Lambda Deployment

To deploy the lambda code you can run the supplied `upload_lambda.sh` script, or modify it for your purposes.

The lambda deployment process requires an s3 bucket to store the code before deployment, so before running the script you need create the bucket and export a `BUCKET` environment variable for the script to use.

for example:

```
export REGION=ap-southeast-2
export BUCKET="lambdabucket1978"
aws s3 mb s3://${BUCKET} --region ${REGION}
```

Once the `BUCKET` and optional `REGION` variables are set, when you run `upload_lambda.sh` it will do the following:

* Use `cloudformation package` to zip up the application and upload to s3
* As part of the `cloudformation package`, a new `generated-cloudformation.yaml` file will be created with the `CodeUri` pointing at the newly uploaded zip file
* Invoke a `cloudformation deploy` to execute the creation of a new stack named `aws-backup-lambda`

Once complete, you should have a new CloudFormation stack, which will have created the Lambda and all required AWS assets

# Configuring the lambda

The configuration of the lambda is detailed in the `cloudformation.yaml` template file.

Things you might want to review and change are:

* The frequency of the backups
* The tags used for the snapshots
* The date based labels to use for each snapshot
* The number of snapshots to keep
* Enable / Disable the EBS or RDS backup function

The configuration for the Lambda is managed as the `Input` passed to the function from the Scheduled event trigger.

An example configuration might be:

```
{
    "period_label": "day",
    "period_format": "%a",
    "keep_count": 14,

    "region_name": "ap-southeast-2",

    "ebs_tag_name": "MakeSnapshot",
    "ebs_tag_value": "True",

    "rds_tag_name": "MakeSnapshot",
    "rds_tag_value": "True"
}
```

* `period_label` is used to identify all backups in the same set, ensure this is UNIQUE across each scheduled event
* `period_format` is the format of the current time to apply to each of the backups, more detail below
* `keep_count` the number of snapshots to keep for each `period_label`
* `region_name` AWS Region
* `tag_name` the RDS and EBS items need to have this tag name to be considered part of the backup
* `tag_value` the RDS and EBS items need to have this tag value to be considered part of the backup


## Supported AWS services

Both EBS and RDS Snapshot management is supported and enabled by default.

Control of which service is executed is identified by the suppling the tags.

 * Supply `ebs_tag_name` and `ebs_tag_value` to run the EBS snapshot process
 * Supply `rds_tag_name` and `rds_tag_value` to run the RDS snapshot process

*Note:* Currently the backup function only runs against a single region, you could easily add another copy of the function to run against an additional region.

*Note:* You can use the same or different sets of key/value for EBS and RDS snapshots. This is useful for snapshotting Kubernetes PVs that do not have tags assigned by you.


## Backup label format

The syntax used to label the backups with a value indicating the current time of the backup.

Any python time format string will be supported, with a set of suggested values shown below.

Suggested settings:

 * More often than a day: `%a%H` - show the day of the week and the hour of the day
 * Daily: `%a` - show the day of the week
 * Weekly: `%U` - show the week of the year
 * Monthly: `%b` - show the month
