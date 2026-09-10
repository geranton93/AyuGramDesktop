// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ui/message_history/history_item.h"

#include "history/history_item.h"
#include "api/api_text_entities.h"
#include "ayu/data/entities.h"
#include "ayu/ui/message_history/history_inner.h"
#include "ayu/utils/ayu_mapper.h"
#include "base/unixtime.h"
#include "core/application.h"
#include "core/click_handler_types.h"
#include "data/data_channel.h"
#include "data/data_file_origin.h"
#include "data/data_forum_topic.h"
#include "data/data_document.h"
#include "data/data_photo.h"
#include "data/data_session.h"
#include "data/data_user.h"
#include "history/history.h"
#include "history/view/history_view_element.h"
#include "ui/basic_click_handlers.h"
#include "ui/text/text_utilities.h"

#include <algorithm>

namespace MessageHistory {

OwnedItem::OwnedItem(std::nullptr_t) {
}

OwnedItem::OwnedItem(
	not_null<HistoryView::ElementDelegate*> delegate,
	not_null<HistoryItem*> data)
	: _data(data), _view(_data->createView(delegate)) {
}

OwnedItem::OwnedItem(OwnedItem &&other)
	: _data(base::take(other._data)), _view(base::take(other._view)) {
}

OwnedItem &OwnedItem::operator=(OwnedItem &&other) {
	_data = base::take(other._data);
	_view = base::take(other._view);
	return *this;
}

OwnedItem::~OwnedItem() {
	clearView();
	if (_data) {
		_data->destroy();
	}
}

void OwnedItem::refreshView(
	not_null<HistoryView::ElementDelegate*> delegate) {
	_view = _data->createView(delegate);
}

void OwnedItem::clearView() {
	_view = nullptr;
}

void GenerateItems(
	not_null<HistoryView::ElementDelegate*> delegate,
	not_null<History*> history,
	AyuMessageBase message,
	Fn<void(OwnedItem item, TimeId sentDate, MsgId)> callback) {
	PeerData *from = history->owner().userLoaded(message.fromId);
	if (!from) {
		from = history->owner().channelLoaded(message.fromId);
	}
	if (!from) {
		from = reinterpret_cast<PeerData*>(history->owner().chatLoaded(message.fromId));
	}
	const auto date = message.entityCreateDate;
	const auto addPart = [&](
		not_null<HistoryItem*> item,
		TimeId sentDate = 0,
		MsgId realId = MsgId())
	{
		return callback(OwnedItem(delegate, item), sentDate, realId);
	};

	const auto resolveMedia = [&]() -> MTPMessageMedia {
		if (message.documentType == AyuMapper::kDocumentTypeNone
			|| message.documentSerialized.empty()) {
			return MTP_messageMediaEmpty();
		}

		if (message.documentType == AyuMapper::kDocumentTypePhoto) {
			auto result = MTP_messageMediaEmpty();
			if (const auto input = AyuMapper::deserializeInputPhoto(
				message.documentSerialized)) {
				input->match([&](const MTPDinputPhoto &source) {
					const auto photo = history->owner().photo(source.vid().v);
					if (photo->id != source.vid().v) {
						return;
					}

					auto thumbs = AyuMapper::deserializeMediaThumbs(
						message.thumbsSerialized);
					const auto dc = (message.mediaDc > 0)
						? message.mediaDc
						: photo->getDC();
					const auto date = (message.date > 0)
						? message.date
						: int(photo->date());
					if (dc <= 0 || date <= 0) {
						return;
					}

					using Flag = MTPDmessageMediaPhoto::Flag;
					result = MTP_messageMediaPhoto(
						MTP_flags(Flag::f_photo),
						MTP_photo(
							MTP_flags(0),
							MTP_long(source.vid().v),
							MTP_long(source.vaccess_hash().v),
							MTP_bytes(source.vfile_reference().v),
							MTP_int(date),
							thumbs
								? std::move(thumbs->photos)
								: MTPVector<MTPPhotoSize>(),
							thumbs
								? std::move(thumbs->videos)
								: MTPVector<MTPVideoSize>(),
							MTP_int(dc)),
						MTPint(),
						MTPDocument());
				}, [](const MTPDinputPhotoEmpty &) {});
			}
			return result;
		}
		if (message.documentType < AyuMapper::kDocumentTypeVideoNote
			|| message.documentType > AyuMapper::kDocumentTypeFile) {
			return MTP_messageMediaEmpty();
		}

		auto result = MTP_messageMediaEmpty();
		if (const auto input = AyuMapper::deserializeInputDocument(
			message.documentSerialized)) {
			input->match([&](const MTPDinputDocument &source) {
				const auto document = history->owner().document(source.vid().v);
				if (document->id != source.vid().v) {
					return;
				}

				auto thumbs = AyuMapper::deserializeMediaThumbs(
					message.thumbsSerialized);
				auto attributes = AyuMapper::deserializeDocumentAttributes(
					message.documentAttributesSerialized);
				const auto dc = (message.mediaDc > 0)
					? message.mediaDc
					: document->getDC();
				const auto date = (message.date > 0)
					? message.date
					: document->date;
				const auto size = message.mediaSize > 0
					? message.mediaSize
					: document->size;
				if (message.mimeType.size()
					> AyuMapper::kMaxMediaTextBytes) {
					return;
				}
				const auto mime = message.mimeType.empty()
					? document->mimeString()
					: QString::fromUtf8(
						message.mimeType.data(),
						int(message.mimeType.size()));
				if (dc <= 0
					|| date <= 0
					|| size < 0
					|| size > AyuMapper::kMaxMediaFileSize
					|| mime.toUtf8().size()
						> static_cast<int>(AyuMapper::kMaxMediaTextBytes)) {
					return;
				}

				const auto documentAttributes = attributes
					? std::move(attributes->v)
					: AyuMapper::buildDocumentAttributes(document);
				using Flag = MTPDmessageMediaDocument::Flag;
				result = MTP_messageMediaDocument(
					MTP_flags(Flag::f_document),
					MTP_document(
						MTP_flags(0),
						MTP_long(source.vid().v),
						MTP_long(source.vaccess_hash().v),
						MTP_bytes(source.vfile_reference().v),
						MTP_int(date),
						MTP_string(mime),
						MTP_long(size),
						thumbs
							? std::move(thumbs->photos)
							: MTPVector<MTPPhotoSize>(),
						thumbs
							? std::move(thumbs->videos)
							: MTPVector<MTPVideoSize>(),
						MTP_int(dc),
						MTP_vector<MTPDocumentAttribute>(
							std::move(documentAttributes))),
					MTPVector<MTPDocument>(),
					MTPPhoto(),
					MTPint(),
					MTPint());
			}, [](const MTPDinputDocumentEmpty &) {});
		}
		return result;
	};

	const auto makeSimpleTextMessage = [&](TextWithEntities &&text,
			MTPMessageMedia &&media)
	{
		base::flags<MessageFlag> flags = MessageFlag::AdminLogEntry;
		if (from) {
			flags |= MessageFlag::HasFromId;
		} else {
			flags |= MessageFlag::HasPostAuthor;
		}
		if (!message.postAuthor.empty()) {
			flags |= MessageFlag::HasPostAuthor;
		}

		return history->makeMessage({
			.id = history->nextNonHistoryEntryId(),
			.flags = flags,
			.from = from ? from->id : 0,
			.date = date,
			.postAuthor = !message.postAuthor.empty()
				? QString::fromStdString(message.postAuthor)
				: from
				? QString()
				: u"unknown user: %1"_q.arg(message.fromId),
		},
			std::move(text),
			std::move(media));
	};

	const auto addSimpleTextMessage = [&](TextWithEntities &&text)
	{
		addPart(makeSimpleTextMessage(std::move(text), resolveMedia()));
	};

	const auto text = QString::fromStdString(message.text);
	auto textAndEntities = Ui::Text::WithEntities(text);
	const auto entities = AyuMapper::deserializeTextWithEntities(message.textEntities);
	textAndEntities.entities = Api::EntitiesFromMTP(&history->session(), entities.v);
	addSimpleTextMessage(std::move(textAndEntities));
}

} // namespace MessageHistory
