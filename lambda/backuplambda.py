from __future__ import print_function

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
import os
import functools

import boto3


class BaseBackupManager(object):
    def __init__(self, period, tag_name, tag_value, date_suffix, keep_count):

        # Message to return result
        self.message = ""
        self.errmsg = ""

        self.period = period
        self.tag_name = tag_name
        self.tag_value = tag_value
        self.date_suffix = date_suffix
        self.keep_count = keep_count

    def lookup_period_prefix(self):
        return self.period

    def get_resource_tags(self, resource_id):
        pass

    def set_resource_tags(self, resource, tags):
        pass

    def get_backable_resources(self):
        pass

    def snapshot_resource(self, resource, description, tags):
        pass

    def list_snapshots_for_resource(self, resource):
        pass

    def resolve_backupable_id(self, resource):
        pass

    def resolve_snapshot_name(self, resource):
        pass

    def resolve_snapshot_time(self, resource):
        return resource['StartTime']

    def process_backup(self):
        # Setup logging
        start_message = 'Started taking %(period)s snapshots at %(date)s' % {
            'period': self.period,
            'date': datetime.today().strftime('%d-%m-%Y %H:%M:%S')
        }
        self.message = start_message + "\n\n"
        print(start_message)

        # Counters
        total_creates = 0
        total_deletes = 0
        count_errors = 0

        # Number of snapshots to keep
        count_success = 0
        count_total = 0

        backupables = self.get_backable_resources()
        for backup_item in backupables:

            count_total += 1
            backup_id = self.resolve_backupable_id(backup_item)

            self.message += 'Processing backup item %(id)s\n' % {
                'id': backup_id
            }

            try:
                tags_volume = self.get_resource_tags(backup_item)
                description = '%(period)s_snapshot %(item_id)s_%(period)s_%(date_suffix)s by snapshot script at %(date)s' % {
                    'period': self.period,
                    'item_id': backup_id,
                    'date_suffix': self.date_suffix,
                    'date': datetime.today().strftime('%d-%m-%Y %H:%M:%S')
                }
                try:
                    self.snapshot_resource(resource=backup_item, description=description, tags=tags_volume)
                    self.message += '    New Snapshot created with description: %s and tags: %s\n' % (
                        description, str(tags_volume))
                    total_creates += 1
                except Exception as e:
                    print("Unexpected error:", sys.exc_info()[0])
                    print(e)
                    exc_type, exc_value, exc_traceback = sys.exc_info()
                    traceback.print_exception(exc_type, exc_value, exc_traceback,
                                              limit=2, file=sys.stdout)
                    pass

                snapshots = self.list_snapshots_for_resource(resource=backup_item)
                deletelist = []

                # Sort the list based on the dates of the objects
                snapshots.sort(key=functools.cmp_to_key(self.date_compare))

                for snap in snapshots:
                    sndesc = self.resolve_snapshot_name(snap)
                    if sndesc.startswith(self.lookup_period_prefix()):
                        deletelist.append(snap)
                    else:
                        print('  Skipping other backup schedule: ' + sndesc)

                self.message += "\n    Current backups in rotation (keeping {0})\n".format(self.keep_count)
                self.message += "    ---------------------------\n"

                for snap in deletelist:
                    self.message += "    {0} - {1}\n".format(self.resolve_snapshot_name(snap),
                                                             self.resolve_snapshot_time(snap))
                self.message += "    ---------------------------\n"

                deletelist.sort(key=functools.cmp_to_key(self.date_compare))
                delta = len(deletelist) - self.keep_count

                for i in range(delta):
                    self.message += '    Deleting snapshot ' + self.resolve_snapshot_name(deletelist[i]) + '\n'
                    self.delete_snapshot(deletelist[i])
                    total_deletes += 1
                    # time.sleep(3)
            except Exception as ex:
                print("Unexpected error:", sys.exc_info()[0])
                print(ex)
                exc_type, exc_value, exc_traceback = sys.exc_info()
                traceback.print_exception(exc_type, exc_value, exc_traceback,
                                          limit=2, file=sys.stdout)
                logging.error('Error in processing volume with id: ' + backup_id)
                self.errmsg += 'Error in processing volume with id: ' + backup_id
                count_errors += 1
            else:
                count_success += 1

        result = '\nFinished making snapshots at %(date)s with %(count_success)s snapshots of %(count_total)s possible.\n\n' % {
            'date': datetime.today().strftime('%d-%m-%Y %H:%M:%S'),
            'count_success': count_success,
            'count_total': count_total
        }

        self.message += result
        self.message += "\nTotal snapshots created: " + str(total_creates)
        self.message += "\nTotal snapshots errors: " + str(count_errors)
        self.message += "\nTotal snapshots deleted: " + str(total_deletes) + "\n"

        return {
            "total_resources": count_total,
            "total_creates": total_creates,
            "total_errors": count_errors,
            "total_deletes": total_deletes,
        }

    def delete_snapshot(self, snapshot):
        pass


class EC2BackupManager(BaseBackupManager):
    def __init__(self, region_name, period, tag_name, tag_value, date_suffix, keep_count):
        super(EC2BackupManager, self).__init__(period=period,
                                               tag_name=tag_name,
                                               tag_value=tag_value,
                                               date_suffix=date_suffix,
                                               keep_count=keep_count)

        # Connect to AWS using the credentials provided above or in Environment vars or using IAM role.
        print('Connecting to AWS')
        self.conn = boto3.client('ec2', region_name=region_name)

    @staticmethod
    def date_compare(snap1, snap2):
        if snap1['StartTime'] < snap2['StartTime']:
            return -1
        elif snap1['StartTime'] == snap2['StartTime']:
            return 0
        return 1

    def lookup_period_prefix(self):
        return self.period + "_snapshot"

    def get_resource_tags(self, resource):
        resource_id = self.resolve_backupable_id(resource)
        resource_tags = {}
        if resource_id:
            tags = self.conn.describe_tags(Filters=[{"Name": "resource-id",
                                                     "Values": [resource_id]}])
            for tag in tags["Tags"]:
                # Tags starting with 'aws:' are reserved for internal use
                if not tag['Key'].startswith('aws:'):
                    resource_tags[tag['Key']] = tag['Value']
        return resource_tags

    def set_resource_tags(self, resource, tags):
        resource_id = resource['SnapshotId']
        for tag_key, tag_value in tags.items():
            print('Tagging %(resource_id)s with [%(tag_key)s: %(tag_value)s]' % {
                'resource_id': resource_id,
                'tag_key': tag_key,
                'tag_value': tag_value
            })

            self.conn.create_tags(Resources=[resource_id],
                                  Tags=[{"Key": tag_key, "Value": tag_value}])

    def share_snapshot(self, resource, ext_account):
        resource_id = resource['SnapshotId']
        print('Sharing %(resource_id)s with %(ext_account)s' % {
            'resource_id': resource_id,
            'ext_account': ext_account
        })

        self.conn.modify_snapshot_attribute(SnapshotId=resource_id,
                              Attribute='createVolumePermission',
                              OperationType='add',
                              UserIds=[
                                  ext_account
                              ])

    def get_backable_resources(self):
        # Get all the volumes that match the tag criteria
        print('Finding volumes that match the requested tag ({ "tag:%(tag_name)s": "%(tag_value)s" })' % {
            'tag_name': self.tag_name,
            'tag_value': self.tag_value
        })
        volumes = self.conn.describe_volumes(Filters=[{"Name": 'tag:' + self.tag_name,
                                                       "Values": [self.tag_value]}])["Volumes"]

        print('Found %(count)s volumes to manage' % {'count': len(volumes)})

        return volumes

    def snapshot_resource(self, resource, description, tags):
        current_snap = self.conn.create_snapshot(VolumeId=self.resolve_backupable_id(resource),
                                                 Description=description)
        self.set_resource_tags(current_snap, tags)
        try:
            ext_account = (os.environ['EXT_ACCOUNT'])
            self.share_snapshot(current_snap, ext_account)
        except KeyError:
            pass

    def list_snapshots_for_resource(self, resource):
        snapshots = self.conn.describe_snapshots(Filters=[
            {"Name": "volume-id",
             "Values": [self.resolve_backupable_id(resource)]
             }])

        return snapshots['Snapshots']

    def resolve_backupable_id(self, resource):
        return resource["VolumeId"]

    def resolve_snapshot_name(self, resource):
        return resource['Description']

    def resolve_snapshot_time(self, resource):
        return resource['StartTime']

    def delete_snapshot(self, snapshot):
        self.conn.delete_snapshot(SnapshotId=snapshot["SnapshotId"])


class RDSBackupManager(BaseBackupManager):
    account_number = None

    def __init__(self, region_name, period, tag_name, tag_value, date_suffix, keep_count):
        super(RDSBackupManager, self).__init__(period=period,
                                               tag_name=tag_name,
                                               tag_value=tag_value,
                                               date_suffix=date_suffix,
                                               keep_count=keep_count)

        # Connect to AWS using the credentials provided above or in Environment vars or using IAM role.
        print('Connecting to AWS')
        self.conn = boto3.client('rds', region_name=region_name)

    @staticmethod
    def date_compare(snap1, snap2):
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        if snap1.get('SnapshotCreateTime', now) < snap2.get('SnapshotCreateTime', now):
            return -1
        elif snap1.get('SnapshotCreateTime', now) == snap2.get('SnapshotCreateTime', now):
            return 0
        return 1

    def lookup_period_prefix(self):
        return self.period

    def get_resource_tags(self, resource):
        resource_id = self.resolve_backupable_id(resource)
        resource_tags = {}

        if 'DBClusterIdentifier' in resource:
            rds_type = 'cluster'
        else:
            rds_type = 'db'

        if resource_id:
            arn = self.build_arn_for_id(resource_id, rds_type)
            tags = self.conn.list_tags_for_resource(ResourceName=arn)['TagList']

            for tag in tags:
                # Tags starting with 'aws:' are reserved for internal use
                if not tag['Key'].startswith('aws:'):
                    resource_tags[tag['Key']] = tag['Value']
        return resource_tags

    # This seems to not be in use for RDS
    def set_resource_tags(self, resource, tags):
        resource_id = resource['SnapshotId']
        for tag_key, tag_value in tags.items():
            print('Tagging %(resource_id)s with [%(tag_key)s: %(tag_value)s]' % {
                'resource_id': resource_id,
                'tag_key': tag_key,
                'tag_value': tag_value
            })

            self.conn.create_tags(Resources=[resource_id],
                                  Tags=[{"Key": tag_key, "Value": tag_value}])

    def share_snapshot(self, resource, ext_account):
        print('Sharing RDS Snapshot with %(ext_account)s' % {
            'ext_account': ext_account
        })
        if 'DBClusterSnapshotIdentifier' in resource:
            resource_id = resource['DBClusterSnapshotIdentifier']
            self.conn.modify_db_cluster_snapshot_attribute(DBClusterSnapshotIdentifier=resource_id,
                                  AttributeName='restore',
                                  ValuesToAdd=[
                                      ext_account
                                  ])
        else:
            resource_id = resource['DBSnapshotIdentifier']
            self.conn.modify_db_snapshot_attribute(DBSnapshotIdentifier=resource_id,
                                  AttributeName='restore',
                                  ValuesToAdd=[
                                      ext_account
                                  ])

    def get_backable_resources(self):
        # Get all the RDSes that match the tag criteria
        print('Finding databases that match the requested tag ({ "tag:%(tag_name)s": "%(tag_value)s" })' % {
            'tag_name': self.tag_name,
            'tag_value': self.tag_value
        })
        found = []

        # Process Aurora clusters
        all_clusters = self.conn.describe_db_clusters()['DBClusters']
        for cluster in all_clusters:
            if self.db_has_tag(cluster):
                found.append(cluster)

        # Process non-Aurora DB instances
        all_instances = self.conn.describe_db_instances()['DBInstances']
        for db_instance in all_instances:
            # prevent adding instances belonging to cluster
            if 'DBClusterIdentifier' not in db_instance:
                if self.db_has_tag(db_instance):
                    found.append(db_instance)

        print('Found %(count)s databases to manage' % {'count': len(found)})

        return found

    def snapshot_resource(self, resource, description, tags):

        aws_tagset = []
        for k in tags:
            aws_tagset.append({"Key": k, "Value": tags[k]})

        date = datetime.today().strftime('%d-%m-%Y-%H-%M-%S')
        snapshot_id = self.period + '-' + self.resolve_backupable_id(resource) + "-" + date + "-" + self.date_suffix

        if 'DBClusterIdentifier' in resource:
            current_snap = self.conn.create_db_cluster_snapshot(
                DBClusterIdentifier=self.resolve_backupable_id(resource),
                DBClusterSnapshotIdentifier=snapshot_id,
                Tags=aws_tagset)['DBClusterSnapshot']
        else:
            current_snap = self.conn.create_db_snapshot(DBInstanceIdentifier=self.resolve_backupable_id(resource),
                                                        DBSnapshotIdentifier=snapshot_id,
                                                        Tags=aws_tagset)['DBSnapshot']
        try:
            ext_account = (os.environ['EXT_ACCOUNT'])
            self.share_snapshot(current_snap, ext_account)
        except KeyError:
            pass

    def list_snapshots_for_resource(self, resource):
        if 'DBClusterIdentifier' in resource:
            snapshots = self.conn.describe_db_cluster_snapshots(
                DBClusterIdentifier=self.resolve_backupable_id(resource),
                SnapshotType='manual')
            return snapshots['DBClusterSnapshots']
        else:
            snapshots = self.conn.describe_db_snapshots(DBInstanceIdentifier=self.resolve_backupable_id(resource),
                                                        SnapshotType='manual')

            return snapshots['DBSnapshots']

    def resolve_backupable_id(self, resource):
        return resource.get("DBClusterIdentifier") or resource.get("DBInstanceIdentifier")

    def resolve_snapshot_name(self, resource):
        return resource.get('DBClusterSnapshotIdentifier') or resource.get('DBSnapshotIdentifier')

    def resolve_snapshot_time(self, resource):
        now = datetime.utcnow()
        return resource.get('SnapshotCreateTime', now)

    def delete_snapshot(self, snapshot):
        if 'DBClusterIdentifier' in snapshot:
            self.conn.delete_db_cluster_snapshot(DBClusterSnapshotIdentifier=snapshot["DBClusterSnapshotIdentifier"])
        else:
            self.conn.delete_db_snapshot(DBSnapshotIdentifier=snapshot["DBSnapshotIdentifier"])

    def db_has_tag(self, db_instance):
        arn = self.build_arn(db_instance)
        tags = self.conn.list_tags_for_resource(ResourceName=arn)['TagList']

        for tag in tags:
            if tag['Key'] == self.tag_name and tag['Value'] == self.tag_value:
                return True

        return False

    def resolve_account_number(self):

        if self.account_number is None:
            groups = self.conn.describe_db_security_groups()['DBSecurityGroups']
            if groups is None or len(groups) == 0:
                self.account_number = 0
            else:
                self.account_number = groups[0]['OwnerId']

        return self.account_number

    def build_arn(self, instance):
        if 'DBClusterIdentifier' in instance:
            return instance['DBClusterArn']
        else:
            return self.build_arn_for_id(instance['DBInstanceIdentifier'],'db')

    def build_arn_for_id(self, instance_id, rds_type):
        # "arn:aws:rds:<region>:<account number>:<resourcetype>:<name>"

        region = self.conn.meta.region_name
        account_number = self.resolve_account_number()

        return "arn:aws:rds:{0}:{1}:{2}:{3}".format(region, account_number, rds_type, instance_id)

def lambda_handler(event, context={}):
    """
    Example content
        {
            "period_label": "day",
            "period_format": "%a%H",

            "region_name": "ap-southeast-2",

            "ec2_tag_name": "MakeSnapshot",
            "ec2_tag_value": "True",

            "rds_tag_name": "Environment",
            "rds_tag_value": "prod",

            "arn": "blart",

            "keep_count": 12
        }
    :param event:
    :param context:
    :return:
    """

    print("Received event: " + json.dumps(event, indent=2))

    period = event["period_label"]
    period_format = event["period_format"]

    ec2_tag_name = event.get('ec2_tag_name', None)
    ec2_tag_value = event.get('ec2_tag_value', None)

    rds_tag_name = event.get('rds_tag_name', None)
    rds_tag_value = event.get('rds_tag_value', None)

    region_name = event['region_name']

    sns_arn = event.get('arn')
    error_sns_arn = event.get('error_arn')
    keep_count = event['keep_count']

    date_suffix = datetime.today().strftime(period_format)

    result = event
    if ec2_tag_name and ec2_tag_value:
        backup_mgr = EC2BackupManager(region_name=region_name,
                                      period=period,
                                      tag_name=ec2_tag_name,
                                      tag_value=ec2_tag_value,
                                      date_suffix=date_suffix,
                                      keep_count=keep_count)

        metrics = backup_mgr.process_backup()

        result["metrics"] = metrics
        result["ec2_backup_result"] = backup_mgr.message
        print('\n' + backup_mgr.message + '\n')

        sns_boto = None

        # Connect to SNS
        if sns_arn or error_sns_arn:
            print('Connecting to SNS')
            sns_boto = boto3.client('sns', region_name=region_name)

        if error_sns_arn and backup_mgr.errmsg:
            sns_boto.publish(TopicArn=error_sns_arn, Message='Error in processing volumes: ' + backup_mgr.errmsg,
                             Subject='Error with AWS Snapshot')

        if sns_arn:
            sns_boto.publish(TopicArn=sns_arn, Message=backup_mgr.message, Subject='Finished AWS EC2 snapshotting')

    if rds_tag_name and rds_tag_value:
        backup_mgr = RDSBackupManager(region_name=region_name,
                                      period=period,
                                      tag_name=rds_tag_name,
                                      tag_value=rds_tag_value,
                                      date_suffix=date_suffix,
                                      keep_count=keep_count)

        metrics = backup_mgr.process_backup()

        result["metrics"] = metrics
        result["rds_backup_result"] = backup_mgr.message
        print('\n' + backup_mgr.message + '\n')

        sns_boto = None

        # Connect to SNS
        if sns_arn or error_sns_arn:
            print('Connecting to SNS')
            sns_boto = boto3.client('sns', region_name=region_name)

        if error_sns_arn and backup_mgr.errmsg:
            sns_boto.publish(TopicArn=error_sns_arn, Message='Error in processing RDS: ' + backup_mgr.errmsg,
                             Subject='Error with AWS Snapshot')

        if sns_arn:
            sns_boto.publish(TopicArn=sns_arn, Message=backup_mgr.message, Subject='Finished AWS RDS snapshotting')

    return json.dumps(result, indent=2)
