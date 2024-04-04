#!/usr/local/bin/python3

import json
import random
import os.path
import argparse
import datetime
import multiprocessing
import traceback
import validators
import pickle
import time
import os, errno
import glob
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
import requests
from flask import Flask, request, session, url_for
from twilio.twiml.messaging_response import MessagingResponse
from crawlerpublic import CrawlerPublic
from sqlitequeue import SqliteQueue
from roundrobinqueue import RoundRobinQueue

WELCOME_MESSAGE = "Welcome to Ella & Sasha's Wedding! Try sending pictures and/or videos."
BACKGROUNDS_PATH = 'backgrounds'
LOW_PRIORITY_DOWNLOAD_QUEUE_DB_PATH = 'db1.sqlite3'
HIGH_PRIORITY_DOWNLOAD_QUEUE_DB_PATH = 'db2.sqlite3'
PICTURES_SMS_PATH = 'pictures/sms'
HOT_FOLDER_PATH = '/Users/gkaftan/Documents/InstantPrint/Pending'
MAX_FEED = 20 # 20
SPOOL_LENGTH = 2 # 2
IGNORE_OLD_POSTS = True
WIDTH, HEIGHT = 1844, 1240

app = Flask(__name__)
app.secret_key = os.urandom(24)


def drop_shadow(image, iterations=2):
    # Create the backdrop image -- a box in the background colour with a shadow on it.
    back = Image.new("RGBA", (WIDTH, HEIGHT), (0x0, 0x0, 0x0, 0x00))

    # Paste shadow
    # shadow = Image.new("RGB", (image.size[0]+30, image.size[1]+30), 0x0)
    shadow = Image.new("RGB", (image.size[0], image.size[1]), 0x0)
    offset = ((back.size[0] - shadow.size[0]) // 2, (back.size[1] - shadow.size[1]) // 2)
    back.paste(shadow, offset)

    # Apply the filter to blur the edges of the shadow.
    n = 0
    while n < iterations:
        back = back.filter(ImageFilter.BoxBlur(100))
        n += 1

    # Paste image in center of empty background of size (WIDTH, HEIGHT)
    offset = ((back.size[0] - image.size[0]) // 2, (back.size[1] - image.size[1]) // 2)
    back.paste(image, offset)

    return back


def get_scaled_size(width, height, img):
    new_width = width
    new_height = int(img.height * (new_width / img.width))
    if new_height > height:
        new_height = height
        new_width = int(img.width * (new_height / img.height))
    return new_width, new_height


def create_print_gaussian(im):
    im_width, im_height = im.size  # Get dimensions
    background = im.convert("RGBA")

    # Landscape (crop top and bottom)
    if im_width > im_height:
        background = background.resize((WIDTH, HEIGHT))
        background = background.filter(ImageFilter.GaussianBlur(225))

    # Portrait
    else:
        # Background
        background = background.rotate(90, expand=True)
        background = background.resize((WIDTH, HEIGHT))
        background = background.filter(ImageFilter.GaussianBlur(225))
        im = im.rotate(90, expand=True)

    # DEBUG
    background = background.convert("RGB")
    return background

    scaled_width, scaled_height = get_scaled_size(int(WIDTH * 0.90), int(HEIGHT * 0.90), im)
    im = im.resize((int(scaled_width * 0.97), int(scaled_height * 0.97)), Image.LANCZOS)

    # Create rectangle abd drop shadow
    rectangle = Image.new('RGB', (scaled_width, scaled_height), (255, 255, 255))
    rectangle = drop_shadow(rectangle)
    background = Image.alpha_composite(background, rectangle)

    # Expand image
    back = Image.new("RGBA", (WIDTH, HEIGHT), (0xff, 0xff, 0xff, 0x00))
    offset = ((back.size[0] - im.size[0]) // 2, (back.size[1] - im.size[1]) // 2)
    back.paste(im, offset)
    im = back

    # Blend image with background
    final = Image.alpha_composite(background, im)
    final = final.convert("RGB")

    return final


def create_print(im, background):
    im_width, im_height = im.size  # Get dimensions
    background = background.convert("RGBA")

    # Landscape (crop top and bottom)
    if im_width > im_height:
        pass

    # Portrait
    else:
        # Background
        im = im.rotate(90, expand=True)

    scaled_width, scaled_height = get_scaled_size(int(WIDTH * 0.90), int(HEIGHT * 0.90), im)
    im = im.resize((int(scaled_width * 0.97), int(scaled_height * 0.97)), Image.LANCZOS)

    # Create rectangle abd drop shadow
    rectangle = Image.new('RGB', (scaled_width, scaled_height), (255, 255, 255))
    rectangle = drop_shadow(rectangle)
    background = Image.alpha_composite(background, rectangle)

    # Expand image
    back = Image.new("RGBA", (WIDTH, HEIGHT), (0xff, 0xff, 0xff, 0x00))
    offset = ((back.size[0] - im.size[0]) // 2, (back.size[1] - im.size[1]) // 2)
    back.paste(im, offset)
    im = back

    # Blend image with background
    final = Image.alpha_composite(background, im)
    final = final.convert("RGB")

    return final


def sms_extract_media(values):
    """
    Takes a Twilio POST request, identifies all media content, saves it to disk,
    and returns a list containing tuples of all the media items: [(media_type, extension, filepath), ...].
    """
    results = []
    # Check that SMS has multimedia content.
    if 'SmsMessageSid' not in values:
        return results
    if 'NumMedia' not in values:
        return results
    if values['NumMedia'] == '0':
        return results
    # Multiple items may be encapsulated within a single SMS. Process one by one.
    for idx in range(int(values['NumMedia'])):
        # Extract media types and extension
        if 'MediaContentType{}'.format(idx) not in values:
            continue
        # MediaContentType should be 'media_type/extension'. Check that len=2.
        if len(values['MediaContentType{}'.format(idx)].split('/')) != 2:
            continue
        media_type, extension = values['MediaContentType{}'.format(idx)].split('/')
        # Check that media URL exists
        if 'MediaUrl{}'.format(idx) not in values:
            continue
        # Validate URL
        if not validators.url(values['MediaUrl{}'.format(idx)]):
            continue
        results.append(
            {'url': values['MediaUrl{}'.format(idx)], 'type': media_type, 'extension': extension, 'metadata': values,
             'from': values['From']})
    return list(reversed(results))


@app.route("/sms", methods=['GET', 'POST'])
def sms_reply():
    # Load session values
    session_timestamp = session.get('timestamp', None)
    if session_timestamp:
        # Is an object, needs to be de-serialized
        session_timestamp = pickle.loads(session_timestamp)
    else:
        session_timestamp = datetime.datetime.now()

    session_recent_image = session.get('recent_image', None)
    if session_recent_image:
        # Is an object, needs to be de-serialized
        session_recent_image = json.loads(session_recent_image)
    else:
        session_recent_image = []

    session_state = session.get('state', 0)

    # If last message was more than 5 minutes ago, clear session state.
    time_diff = datetime.datetime.now() - session_timestamp
    time_diff = time_diff.seconds
    if time_diff > (5*60):
        session_state = 0

    # Scan the SMS for any media items. If any pictures (.png or .jpeg) are discovered,
    # they'll be populated in the 'picture' object. It will also note if any other picture
    # format (e.g. gif) or videos were discovered. This is reflected in the 'found_media' list.
    found_media = False
    attachments = sms_extract_media(request.values)
    pictures = []
    for item in attachments:
        if item['type'] == 'image':
            found_media = True
            if item['extension'] == 'jpeg' or item['extension'] == 'png':
                pictures.append(item)
        elif item['type'] == 'video':
            found_media = True

    # All media that was found should be added to the low priority queue
    if attachments:
        low_priority_queue = SqliteQueue(LOW_PRIORITY_DOWNLOAD_QUEUE_DB_PATH)
        for item in attachments:
            low_priority_queue.append(item)

    # If media has been found, we ignore all other previous states (e.g. asking if a
    # picture should be printed, or how many prints). There are two actions that
    # can be taken:
    #    1. New picture was just received, ask whether it should be printed.
    #    2. Video or non-printable image such as a .gif was received, we thank the
    #       user for sharing this content.

    # New photo(s) received.
    if len(pictures):
        session['timestamp'] = pickle.dumps(datetime.datetime.now())
        session['recent_image'] = json.dumps(pictures)
        session['state'] = 1
        response = MessagingResponse()
        if len(pictures) > 1:
            response.message("Thanks for sharing! Would you like to print these {} "
                             "pictures? Reply with a Yes or No.".format(len(pictures)))
        else:
            response.message("Thanks for sharing! Would you like to print this "
                             "picture? Reply with Yes or No.")
        return str(response)

    # Media received without photos
    if found_media:
        session['state'] = 0
        response = MessagingResponse()
        response.message("We've received your message. Thanks for the memories!")
        return str(response)

    # At this point, we may be at the state where we query whether a user would like to
    # print the picture. These are the possible outcomes:
    #   1. User says "yes", "okay", "ok", "sure". Send picture to print queue.
    #   2. User says "no", "nope". No need to print.
    #   3. User says something else. Will need to repeat instructions.
    if session_state == 1:
        if 'Body' in request.values:
            try:
                txt = request.values['Body']
                txt = ''.join(txt.split()).lower()
                if txt in ["yes", "sure", "okay", "ok", "yeah", "k"]:
                    if len(session_recent_image):
                        # The list of pictures is appended to the queue.
                        high_priority_queue = SqliteQueue(HIGH_PRIORITY_DOWNLOAD_QUEUE_DB_PATH)
                        for item in session_recent_image:
                            high_priority_queue.append(item)
                        # Formulate response
                        session['state'] = 0
                        response = MessagingResponse()
                        response.message("Great! Head on over to the print station. In the meantime, here's a joke...")
                        response.redirect(url_for('sms_joke'))
                        return str(response)
                elif txt in ["no", "nope", "nah"]:
                    # Formulate response
                    session['state'] = 0
                    response = MessagingResponse()
                    response.message("No problem. You may share additional pictures at any time.")
                    return str(response)
                else:
                    response = MessagingResponse()
                    if len(session_recent_image) > 1:
                        response.message("Sorry, I don't understand. Would you like to print these {} pictures? Reply "
                                         "with a Yes or No.".format(len(session_recent_image)))
                    else:
                        response.message("Sorry, I don't understand. Would you like to print this picture? Reply "
                                         "with a Yes or No.")
                    return str(response)
            except Exception:
                pass
        session['state'] = 0
        response = MessagingResponse()
        response.message("Uh-oh. Something went wrong. Try again shortly.")
        return str(response)

    # Otherwise, send instructions.
    session['state'] = 0
    response = MessagingResponse()
    response.message(WELCOME_MESSAGE)
    return str(response)


@app.route("/joke", methods=['GET', 'POST'])
def sms_joke():
    response = MessagingResponse()
    response.message(random.choice(jokes))
    return str(response)


def sms_process():
    app.run(threaded=True)


def instagram_process():
    crawler = None
    old_entries = None

    # Periodically scan for new posts
    while True:
        try:
            if not crawler:
                crawler = CrawlerPublic()
                crawler.connect()
            # Old photos already tagged prior to script being started will not be printed. Script will need
            # to scan feed and mark photos already there.
            if IGNORE_OLD_POSTS:
                if old_entries is None:
                    posts = crawler.get_feed(args.hashtag, count=(MAX_FEED + 30))
                    print("Ignoring old Instagram posts...")
                    old_entries = set()
                    if len(posts):
                        for post in posts:
                            if post['post_id'] not in old_entries:
                                old_entries.add(post['post_id'])
                    time.sleep(random.uniform(3.0, 3.5))
            else:
                old_entries = set()
            posts = crawler.get_feed(args.hashtag, count=MAX_FEED)
            if len(posts):
                posts = list(reversed(posts))
                high_priority_queue = SqliteQueue(HIGH_PRIORITY_DOWNLOAD_QUEUE_DB_PATH)
                for post in posts:
                    if post['post_id'] not in old_entries:
                        old_entries.add(post['post_id'])
                        high_priority_queue.append(post)
        except Exception:
            crawler = None
            traceback.print_exc()
            pass

        time.sleep(random.uniform(3.0, 3.5))


def download_process():
    # Load backgrounds
    backgrounds = glob.glob(BACKGROUNDS_PATH + "/*.png")

    high_priority = RoundRobinQueue()
    low_priority = RoundRobinQueue()
    media_cache = {}
    low_priority_queue = SqliteQueue(LOW_PRIORITY_DOWNLOAD_QUEUE_DB_PATH)
    high_priority_queue = SqliteQueue(HIGH_PRIORITY_DOWNLOAD_QUEUE_DB_PATH)
    counter = 0
    print_queue = RoundRobinQueue()
    print_spool = []

    while True:
        did_work = False
        # Place requests in downloaded queues.
        try:
            if low_priority_queue.peek():
                picture = low_priority_queue.popleft()
                low_priority.push(picture['from'], picture)
                did_work = True
            if high_priority_queue.peek():
                picture = high_priority_queue.popleft()
                high_priority.push(picture['from'], picture)
                did_work = True
        except Exception:
            traceback.print_exc()
            pass

        # Routine to download media items. High priority items will always have priority.
        for_print, picture = False, None
        try:
            if len(high_priority):
                picture = high_priority.peek()
                for_print = True
            elif len(low_priority):
                picture = low_priority.peek()
            if picture:
                did_work = True
                # Only download item if it hasn't been previously downloaded.
                if picture['url'] not in media_cache:
                    print("Downloading: {}".format(picture['url']))
                    counter += 1
                    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    path = '{}/{}_{}'.format(PICTURES_SMS_PATH, timestamp, counter)
                    picture['cached'] = '{}.{}'.format(path, picture['extension'])
                    # Save metadata
                    with open('{}.{}'.format(path, 'json'), 'w') as file:
                        file.write(json.dumps(picture, indent=3, sort_keys=True))
                    # Download media content from URL
                    media = requests.get(picture['url']).content
                    # Save media
                    with open(picture['cached'], 'wb') as file:
                        file.write(media)
                    # Add downloaded item to cache.
                    media_cache[picture['url']] = picture
                    print("Download Successful: {}".format(picture['cached']))
                # Add to print queue if for printing.
                if for_print:
                    print("Queueing for Print: {}".format(media_cache[picture['url']]['cached']))
                    print_queue.push(media_cache[picture['url']]['from'], media_cache[picture['url']])
                # Remove picture from list
                if for_print:
                    high_priority.pop()
                else:
                    low_priority.pop()
        except Exception:
            traceback.print_exc()
            pass

        # Check if the print job has completed. This is determined by checking whether the
        # printer hot folder contains the item to be printed.
        if len(print_spool):
            for job in print_spool:
                if os.path.exists(job['path']) is not True:
                    # Check that elapsed time is greater than 8 seconds.
                    time_diff = datetime.datetime.now() - job['time']
                    time_diff = time_diff.seconds
                    if time_diff > 8:
                        # Remove if greater than 8 seconds.
                        print("Completed Printing: {}".format(job['path']))
                        print_spool.remove(job)
                        did_work = True

        if (len(print_spool) < SPOOL_LENGTH) and len(print_queue):
            try:
                # To-Do: Combine photo with template
                picture = print_queue.pop()
                im = Image.open(picture['cached'])
                background = Image.open(backgrounds[0])
                background = background.convert("RGBA")
                backgrounds.append(backgrounds[0])
                backgrounds.pop(0)
                im = create_print(im, background)
                # im = create_print_gaussian(im)

                # Save to hot folder
                path = "{}/{}.png".format(HOT_FOLDER_PATH, os.path.splitext(os.path.basename(picture['cached']))[0])
                im.save(path)
                print_spool.append({'path': path, 'time': datetime.datetime.now()})
                print("Spooling: {}".format(path))
                time.sleep(0.3)
                did_work = True
            except Exception:
                traceback.print_exc()
                pass

        if not did_work:
            time.sleep(0.1)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Hashtag Printer')
    parser.add_argument('-hashtag', '--hashtag', dest='hashtag', type=str, required=False)
    args = parser.parse_args()

    # Load jokes list
    with open('jokes.txt') as f:
        jokes = f.read().splitlines()

    # Remove old databases
    try:
        os.remove(LOW_PRIORITY_DOWNLOAD_QUEUE_DB_PATH)
        os.remove(HIGH_PRIORITY_DOWNLOAD_QUEUE_DB_PATH)
    except OSError:
        pass

    # Create folders
    try:
        os.makedirs(HOT_FOLDER_PATH)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    try:
        os.makedirs(PICTURES_SMS_PATH)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

    # Start Flask process
    download_process = multiprocessing.Process(target=download_process)
    download_process.start()

    sms_process = multiprocessing.Process(target=sms_process)
    sms_process.start()
    if args.hashtag:
        instagram_process = multiprocessing.Process(target=instagram_process)
        instagram_process.start()

    download_process.join()
    sms_process.join()
    if args.hashtag:
        instagram_process.join()

