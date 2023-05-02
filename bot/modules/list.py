from telegram.ext import CommandHandler, run_async
from bot.helper.mirror_utils.gdriveTools import GoogleDriveHelper
from bot import LOGGER, dispatcher


@run_async
def list_drive(update, context):
    message = update.message.text
    search = message.replace('/list ', '')
    LOGGER.info("Searching: " + search)
    gdrive = GoogleDriveHelper(None)
    msg = gdrive.drive_list(search)
    if msg:
        context.bot.send_message(chat_id=update.message.chat_id, reply_to_message_id=update.message.message_id,
                                 text=msg, parse_mode='HTML')

    else:
        context.bot.send_message(chat_id=update.message.chat_id, reply_to_message_id=update.message.message_id,
                                 text="No Results Found.")


list_handler = CommandHandler('list', list_drive)
dispatcher.add_handler(list_handler)
