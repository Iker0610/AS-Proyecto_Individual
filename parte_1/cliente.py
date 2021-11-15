from pymemcache.client import base


def main():
    client = base.Client(('memcached', 11211))
    client.set('welcome_message_v500', 'hello word')


if __name__ == '__main__':
    main()
