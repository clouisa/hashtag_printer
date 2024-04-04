import json
import codecs
import os.path
import traceback
from re import findall
from instagram_web_api import Client
import time, random

class CrawlerPublic:
    def __init__(self):
        self.api = None
        self.next_max_id = None
        self.hashtag = None

    def connect(self):
        # Destroy any previous session
        self.api = None

        try:
            self.api = Client(auto_patch=True, drop_incompat_keys=False)
            print('\033[92m' + "Instagram API successfully initiated!" + '\033[0m')

        except Exception:
            traceback.print_exc()
            raise

    def disconnect(self):
        self.api = None

    def get_posts(self, hashtag):
        try:
            self.hashtag = hashtag
            results = self.api.tag_feed(hashtag)
            # Check if feed is empty
            if not results['data']['hashtag']:
                return []
            with open('posts.json', 'w') as file:
                file.write(json.dumps(results, indent=3))
            results = results.get('data', {}).get('hashtag', {}).get('edge_hashtag_to_media', {})
            # Check for pagination
            if results.get('page_info', {}).get('has_next_page', False):
                self.next_max_id = results.get('page_info').get('end_cursor', 0)
            else:
                self.next_max_id = 0
            # Get posts
            results = results.get('edges', None)
            if results:
                feed = results
                if len(feed) > 0:
                    posts = []
                    for post in feed:
                        post = post.get('node', {})
                        post = self.beautify_post(post)
                        # Video objects will return [] as beautify_post looks at media type. Don't add [] to results.
                        if post:
                            posts.append(post)
                    return posts
                else:
                    return []
            else:
                return []
        except Exception:
            traceback.print_exc()
            raise

    def get_feed(self, hashtag, count):
        posts = self.get_posts(hashtag)
        if not len(posts):
            return []
        # return posts[:count]
        if len(posts) >= count:
            return posts[:count]
        while True:
            time.sleep(random.uniform(0.5, 1.0))
            more_posts = self.get_more_posts()
            if not len(more_posts):
                return posts
            # Check for repeating posts/pagination wrap around
            for post in more_posts:
                for old_post in posts:
                    if post['post_id'] == old_post['post_id']:
                        posts.extend(more_posts)
                        return posts
            # No repeating posts
            posts.extend(more_posts)
            if len(posts) >= count:
                return posts

    def get_more_posts(self):
        try:
            results = self.api.tag_feed(self.hashtag, end_cursor=self.next_max_id)
            # Check if feed is empty
            if not results['data']['hashtag']:
                return []
            results = results.get('data', {}).get('hashtag', {}).get('edge_hashtag_to_media', {})
            # Check for pagination
            if results.get('page_info', {}).get('has_next_page', False):
                self.next_max_id = results.get('page_info').get('end_cursor', 0)
            else:
                self.next_max_id = 0
            # Get posts
            results = results.get('edges', None)
            if results:
                feed = results
                if len(feed) > 0:
                    posts = []
                    for post in feed:
                        post = post.get('node', {})
                        post = self.beautify_post(post)
                        # Video objects will return [] as beautify_post looks at media type. Don't add [] to results.
                        if post:
                            posts.append(post)
                    return posts
                else:
                    return []
            else:
                return []
        except Exception:
            traceback.print_exc()
            raise

    @staticmethod
    def beautify_post(post):
        # Check that post is an image
        if post.get('is_video', True):
            return None
        # Get caption/text
        caption = post.get('edge_media_to_caption', {}).get('edges', [])
        if len(caption) and isinstance(caption[0], dict):
            caption = caption[0].get('node', {}).get('text', "")
        else:
            caption = ""
        processed_media = {
            'post_id': post['id'],
            'url': post['display_url'],
            'extension': "jpeg",
            'caption': caption,
            'from': post.get('owner', {}).get('id'),
            'metadata': post
        }
        processed_media['tags'] = findall(r'#[^#\s]*', processed_media['caption'])
        return processed_media

    @staticmethod
    def to_json(python_object):
        if isinstance(python_object, bytes):
            return {'__class__': 'bytes',
                    '__value__': codecs.encode(python_object, 'base64').decode()}
        raise TypeError(repr(python_object) + ' is not JSON serializable')

    @staticmethod
    def from_json(json_object):
        if '__class__' in json_object and json_object['__class__'] == 'bytes':
            return codecs.decode(json_object['__value__'].encode(), 'base64')
        return json_object

    def onlogin_callback(self, api, new_settings_file):
        cache_settings = api.settings
        with open(new_settings_file, 'w') as outfile:
            json.dump(cache_settings, outfile, default=self.to_json)
            # print('SAVED: {0!s}'.format(new_settings_file))
