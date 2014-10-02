#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SimpleStepper backend main script.
"""

import httplib
import json
import os

import boto.ec2
import boto.exception
import tornado.httpserver
import tornado.options
import tornado.web
import tornado.ioloop
import tornado_cors


# define options
tornado.options.define(
    'config_file',
    default='./config.py',
    help='Configuration file path.'
)
tornado.options.define(
    'port',
    default=8080,
    help='Listen port number.'
)
tornado.options.define(
    'region_name',
    default='us-east-1',
    help='AWS region name.'
)
tornado.options.define(
    'aws_access_key_id',
    default='AWS_ACCESS_KEY_ID',
    help='AWS access_key_id.'
)
tornado.options.define(
    'aws_secret_access_key',
    default='AWS_SECRET_ACCESS_KEY',
    help='AWS secret_access_key.'
)
tornado.options.define(
    'target_security_group_ids',
    default=list(),
    help='Target security group ids.'
)
tornado.options.define(
    'development',
    default=False,
    help='If you are developer, set true to this option.'
)


# utils
def parse_security_groups(conn, security_group_ids):
    """
    Parse raw security group values with following format (json like).
    {
      "results": [
        {
          "name": "security-group01",
          "id": "sg-XXXXXXXX",
          "rules": [
            {
              "source": "127.0.0.1/32",
              "protocol": "tcp",
              "port": "22 - 22"
            },
            {
              "source": "127.0.0.1/32",
              "protocol": "tcp",
              "port": "80 - 80"
            }
          }
        ]
      ]
    }

    :param conn: AWS connection object
    :type conn: boto.ec2.EC2Connection
    :param security_group_ids: AWS security group ids
    :type security_group_ids: list
    :return: Parsed security group rules (json like)
    :rtype: dict
    """
    result = list()
    response = conn.get_all_security_groups(
        group_ids=security_group_ids
    )
    for raw_security_group in response:
        security_group = dict()
        security_group['name'] = raw_security_group.name
        security_group['id'] = raw_security_group.id
        security_group['rules'] = list()
        for rule in raw_security_group.rules:
            for entry in rule.grants:
                security_group['rules'].append(
                    {
                        'source': str(entry),
                        'protocol': rule.ip_protocol,
                        'port': '{0} - {1}'.format(
                            rule.from_port,
                            rule.to_port
                        )
                    }
                )
        result.append(security_group)
    result = {
        'results': result
    }
    return result


# handlers
class SGHandler(tornado.web.RequestHandler):

    def initialize(self,
                   region_name,
                   aws_access_key_id,
                   aws_secret_access_key,
                   target_security_group_ids):
        self.region_name = region_name
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.target_security_group_ids = target_security_group_ids
        self.conn = None

    def get_ec2_connection(self):
        if self.conn is None:
            self.conn = boto.ec2.connect_to_region(
                region_name=self.region_name,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key
            )

    def get(self):
        try:
            self.get_ec2_connection()
            parsed_security_groups = parse_security_groups(
                conn=self.conn,
                security_group_ids=self.target_security_group_ids
            )
            self.finish(json.dumps(parsed_security_groups))
        except boto.exception.EC2ResponseError as exception:
            self.set_status(httplib.BAD_REQUEST)
            self.finish(
                {
                    'status_code': self.get_status(),
                    'message': exception.error_message
                }
            )
        except Exception as exception:
            self.set_status(httplib.INTERNAL_SERVER_ERROR)
            self.finish(
                {
                    'status_code': self.get_status(),
                    'message': exception.__str__()
                }
            )

    def post(self):
        try:
            remote_ip = None
            if (
                'X-FORWARDED-FOR' in
                [entry.upper() for entry in self.request.headers.keys()]
            ):
                remote_ip = self.request.headers.get('X-FORWARDED-FOR')
            else:
                remote_ip = self.request.remote_ip

            if remote_ip is None:
                self.set_status(httplib.INTERNAL_SERVER_ERROR)
                self.finish(
                    {
                        'status_code': self.get_status(),
                        'message': 'Sorry, could not get Your IP Address.'
                    }
                )
            conn = boto.ec2.connect_to_region(
                region_name=self.region_name,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key
            )
            response = conn.get_all_security_groups(
                group_ids=self.target_security_group_ids
            )
            for entry in response:
                entry.authorize(
                    ip_protocol='tcp',
                    from_port=22,
                    to_port=22,
                    cidr_ip=('{0}/32'.format(remote_ip))
                )
            message = (
                'Your IP {ip} is appended to {sg}'
                ''.format(
                    ip=remote_ip,
                    sg=tornado.options.options.target_security_group_ids
                )
            )
            self.finish(
                {
                    'status_code': self.get_status(),
                    'message': message
                }
            )

        except boto.exception.EC2ResponseError as exception:
            self.set_status(httplib.BAD_REQUEST)
            self.finish(
                {
                    'status_code': self.get_status(),
                    'message': exception.error_message
                }
            )

        except Exception as exception:
            self.set_status(httplib.INTERNAL_SERVER_ERROR)
            self.finish(
                {
                    'status_code': self.get_status(),
                    'message': exception.__str__()
                }
            )

    def delete(self):
        try:
            conn = boto.ec2.connect_to_region(
                region_name=self.region_name,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key
            )
            results = list()
            current_sgs = conn.get_all_security_groups(
                group_ids=self.target_security_group_ids
            )
            for sg in current_sgs:
                for rule in sg.rules:
                    for cidr_ip in rule.grants:
                        results.append(
                            {
                                'ip_protocol': rule.ip_protocol,
                                'from_port': rule.from_port,
                                'to_port': rule.to_port,
                                'cidr_ip': str(cidr_ip)
                            }
                        )
                        sg.revoke(
                            ip_protocol=rule.ip_protocol,
                            from_port=rule.from_port,
                            to_port=rule.to_port,
                            cidr_ip=cidr_ip
                        )
            self.finish(
                json.dumps({
                    'results': results
                })
            )

        except boto.exception.EC2ResponseError as exception:
            self.set_status(httplib.BAD_REQUEST)
            self.finish(
                {
                    'status_code': self.get_status(),
                    'message': exception.error_message
                }
            )

        except Exception as exception:
            self.set_status(httplib.INTERNAL_SERVER_ERROR)
            self.finish(
                {
                    'status_code': self.get_status(),
                    'message': exception.__str__()
                }
            )


class DevelopmentSGHandler(
    tornado_cors.CorsMixin,
    SGHandler
):
    CORS_ORIGIN = '*'


def main():
    tornado.options.parse_command_line()
    if os.path.exists(tornado.options.options.config_file):
        tornado.options.parse_config_file(tornado.options.options.config_file)
    else:
        raise OSError('{0}: No such file or directory.')

    # handler options
    host_pattern = r'.*'

    SIMPLE_STEPPER_APP = tornado.web.Application()
    if not tornado.options.options.development:
        SIMPLE_STEPPER_APP.add_handlers(
            host_pattern=host_pattern,
            host_handlers=[
                (
                    r'/api/inboundRules', SGHandler,
                    {
                        "region_name":
                            tornado.options.options.region_name,
                        "aws_access_key_id":
                            tornado.options.options.aws_access_key_id,
                        "aws_secret_access_key":
                            tornado.options.options.aws_secret_access_key,
                        "target_security_group_ids":
                            tornado.options.options.target_security_group_ids
                    }
                )
            ]
        )
    else:
        SIMPLE_STEPPER_APP.add_handlers(
            host_pattern=host_pattern,
            host_handlers=[
                (
                    r'/api/inboundRules', DevelopmentSGHandler,
                    {
                        "region_name":
                            tornado.options.options.region_name,
                        "aws_access_key_id":
                            tornado.options.options.aws_access_key_id,
                        "aws_secret_access_key":
                            tornado.options.options.aws_secret_access_key,
                        "target_security_group_ids":
                            tornado.options.options.target_security_group_ids
                    }
                )
            ]
        )
    SIMPLE_STEPPER = tornado.httpserver.HTTPServer(
        SIMPLE_STEPPER_APP
    )
    SIMPLE_STEPPER.listen(tornado.options.options.port)
    tornado.ioloop.IOLoop.instance().start()


if __name__ == '__main__':
    main()
