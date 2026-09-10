// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/download_helper.h"

#include "base/debug_log.h"

#include <QtCore/QCryptographicHash>
#include <QtCore/QDir>
#include <QtCore/QFileInfo>
#include <QtCore/QSaveFile>
#include <QtCore/QSet>
#include <QtCore/QTimer>
#include <QtCore/QUrl>
#include <QtNetwork/QNetworkAccessManager>
#include <QtNetwork/QNetworkReply>
#include <QtNetwork/QNetworkRequest>

#include <algorithm>
#include <chrono>
#include <limits>
#include <mutex>
#include <utility>

namespace Ayu::STT {
namespace {

constexpr qint64 kDefaultMaximumDownloadSize = 512 * 1024 * 1024;
constexpr qint64 kReadChunkSize = 1024 * 1024;
constexpr qint64 kModelHashChunkSize = 1024 * 1024;
constexpr auto kTransferTimeout = std::chrono::seconds(10 * 60);
constexpr std::size_t kMaxActiveDownloads = 4;
QSet<QString> ActiveDestinations;
std::mutex ActiveDestinationsMutex;

[[nodiscard]] bool ReserveDestination(const QString &path) {
	const auto lock = std::lock_guard(ActiveDestinationsMutex);
	if (ActiveDestinations.contains(path)
		|| ActiveDestinations.size() >= kMaxActiveDownloads) {
		return false;
	}
	ActiveDestinations.insert(path);
	return true;
}

void ReleaseDestination(const QString &path) {
	const auto lock = std::lock_guard(ActiveDestinationsMutex);
	ActiveDestinations.remove(path);
}

[[nodiscard]] bool IsPinnedDownloadUrl(const QUrl &url) {
	return url.isValid()
		&& url.scheme() == u"https"_q
		&& url.host() == u"huggingface.co"_q
		&& url.userInfo().isEmpty()
		&& url.port(-1) == -1;
}

class DownloadState final : public QObject {
public:
	DownloadState(
			QString url,
			QString destPath,
			QString sha256Expected,
			qint64 expectedSize,
			std::function<void(int)> onProgress,
			std::function<void(bool)> onDone)
	: _url(std::move(url))
	, _destPath(std::move(destPath))
	, _sha256Expected(std::move(sha256Expected))
	, _expectedSize(expectedSize)
	, _onProgress(std::move(onProgress))
	, _onDone(std::move(onDone))
	, _hash(QCryptographicHash::Sha256)
	, _file(_destPath) {
		_timer.setSingleShot(true);
		_timer.setInterval(std::chrono::duration_cast<std::chrono::milliseconds>(
			kTransferTimeout).count());
		QObject::connect(&_timer, &QTimer::timeout, this, [this] {
			Fail(u"transfer timeout"_q);
		});
	}

	void start() {
		try {
			const auto url = QUrl(_url);
			if (!IsPinnedDownloadUrl(url)
				|| _destPath.isEmpty()
				|| (!_sha256Expected.isEmpty()
					&& !IsValidSha256Hex(_sha256Expected))
				|| (_expectedSize == 0)
				|| (_expectedSize < kUnknownExpectedDownloadSize)
				|| (_expectedSize > kDefaultMaximumDownloadSize)) {
				Fail(u"invalid download parameters"_q);
				return;
			}

			const auto directory = QFileInfo(_destPath).absolutePath();
			if (directory.isEmpty() || !QDir().mkpath(directory)) {
				Fail(u"failed to create destination directory"_q);
				return;
			}
			if (!ReserveDestination(_destPath)) {
				Fail(u"download capacity is busy"_q);
				return;
			}
			_destinationReserved = true;

			_file.setDirectWriteFallback(false);
			if (!_file.open(QIODevice::WriteOnly)) {
				Fail(u"failed to open destination"_q);
				return;
			}

			auto request = QNetworkRequest(url);
			request.setAttribute(
				QNetworkRequest::RedirectPolicyAttribute,
				QNetworkRequest::NoLessSafeRedirectPolicy);
			request.setTransferTimeout(
				std::chrono::duration_cast<std::chrono::milliseconds>(
					kTransferTimeout).count());
			_reply = _network.get(request);
			if (!_reply) {
				Fail(u"failed to create network request"_q);
				return;
			}

			QObject::connect(
				_reply,
				&QNetworkReply::metaDataChanged,
				this,
				[this] { CheckContentLength(); });
			QObject::connect(
				_reply,
				&QNetworkReply::readyRead,
				this,
				[this] { ReadAvailable(); });
			QObject::connect(
				_reply,
				&QNetworkReply::downloadProgress,
				this,
				[this](qint64, qint64 total) {
					if (total > 0) {
						_totalSize = total;
						CheckContentLength();
					}
					ReportProgress();
				});
			QObject::connect(
				_reply,
				&QNetworkReply::finished,
				this,
				[this] { Finish(); });
			_timer.start();
		} catch (...) {
			Complete(false);
		}
	}

private:
	void CheckContentLength() {
		try {
			if (!_reply || _finished) {
				return;
			}
			const auto header = _reply->header(
				QNetworkRequest::ContentLengthHeader);
			auto length = _totalSize;
			if (header.isValid()) {
				bool ok = false;
				length = header.toLongLong(&ok);
				if (!ok || length <= 0) {
					Fail(u"invalid content length"_q);
					return;
				}
			}
			if (length <= 0) {
				return;
			}
			_totalSize = length;
			const auto limit = _expectedSize >= 0
				? _expectedSize
				: kDefaultMaximumDownloadSize;
			if (length > limit
				|| (_expectedSize >= 0 && length != _expectedSize)) {
				Fail(u"content length exceeds expected size"_q);
			}
		} catch (...) {
			Complete(false);
		}
	}

	void ReadAvailable() {
		try {
			if (!_reply || _finished) {
				return;
			}
			while (_reply->bytesAvailable() > 0) {
				const auto chunk = _reply->read(kReadChunkSize);
				if (chunk.isEmpty()) {
					if (_reply->error() != QNetworkReply::NoError) {
						Fail(_reply->errorString());
					}
					return;
				}
				if (_received
					> std::numeric_limits<qint64>::max() - chunk.size()) {
					Fail(u"download size overflow"_q);
					return;
				}
				const auto next = _received + chunk.size();
				const auto limit = _expectedSize >= 0
					? _expectedSize
					: kDefaultMaximumDownloadSize;
				if (next > limit || _file.write(chunk) != chunk.size()) {
					Fail(u"download write failed or size limit reached"_q);
					return;
				}
				_hash.addData(chunk);
				_received = next;
				ReportProgress();
				if (_finished) {
					return;
				}
			}
		} catch (...) {
			Complete(false);
		}
	}

	void ReportProgress() {
		if (!_onProgress || _finished) {
			return;
		}
		const auto total = _expectedSize >= 0 ? _expectedSize : _totalSize;
		if (total <= 0) {
			return;
		}
		const auto percent = static_cast<int>(std::clamp<qint64>(
			_received * 100 / total,
			0,
			100));
		if (percent != _lastPercent) {
			_lastPercent = percent;
			try {
				_onProgress(percent);
			} catch (...) {
				Complete(false);
			}
		}
	}

	void Fail(const QString &reason) {
		if (_finished) {
			return;
		}
		LOG(("DownloadWithProgress: %1: %2").arg(reason, _url));
		Complete(false);
	}

	void Finish() {
		try {
			if (_finished) {
				return;
			}
			ReadAvailable();
			if (_finished) {
				return;
			}
			if (_reply->error() != QNetworkReply::NoError) {
				Fail(_reply->errorString());
				return;
			}
			if ((_expectedSize >= 0 && _received != _expectedSize)
				|| (_expectedSize < 0
					&& _received > kDefaultMaximumDownloadSize)) {
				Fail(u"download size mismatch"_q);
				return;
			}
			const auto expected = QByteArray::fromHex(
				_sha256Expected.toLatin1());
			if (!expected.isEmpty() && _hash.result() != expected) {
				Fail(u"sha256 mismatch"_q);
				return;
			}
			if (!_file.commit()) {
				Fail(u"atomic commit failed"_q);
				return;
			}
			Complete(true);
		} catch (...) {
			Complete(false);
		}
	}

	void Complete(bool ok) {
		if (_finished) {
			return;
		}
		_finished = true;
		_timer.stop();
		if (_destinationReserved) {
			ReleaseDestination(_destPath);
			_destinationReserved = false;
		}
		if (!ok && _file.isOpen()) {
			_file.cancelWriting();
		}
		if (_reply && !_reply->isFinished()) {
			_reply->abort();
		}
		const auto done = std::move(_onDone);
		deleteLater();
		if (done) {
			try {
				done(ok);
			} catch (...) {
				LOG(("DownloadWithProgress: completion callback failed"));
			}
		}
	}

	const QString _url;
	const QString _destPath;
	const QString _sha256Expected;
	const qint64 _expectedSize = kUnknownExpectedDownloadSize;
	const std::function<void(int)> _onProgress;
	std::function<void(bool)> _onDone;
	QNetworkAccessManager _network{ this };
	QNetworkReply *_reply = nullptr;
	QTimer _timer{ this };
	QCryptographicHash _hash;
	QSaveFile _file;
	qint64 _received = 0;
	qint64 _totalSize = 0;
	int _lastPercent = -1;
	bool _finished = false;
	bool _destinationReserved = false;
};

} // namespace

bool IsValidSha256Hex(const QString &value) {
	if (value.size() != 64) {
		return false;
	}
	for (const auto ch : value) {
		if (!((ch >= u'0' && ch <= u'9')
			|| (ch >= u'a' && ch <= u'f')
			|| (ch >= u'A' && ch <= u'F'))) {
			return false;
		}
	}
	return true;
}

void InvokeCallbackSafely(
		const std::function<void(QString)> &callback,
		QString text) {
	if (!callback) {
		return;
	}
	try {
		callback(std::move(text));
	} catch (...) {
		LOG(("STT callback failed"));
	}
}

bool VerifyModelFile(
		const QString &path,
		const qint64 expectedSize,
		const QString &expectedSha256) {
	try {
		if (expectedSize <= 0 || !IsValidSha256Hex(expectedSha256)) {
			return false;
		}
		const auto expected = QByteArray::fromHex(
			expectedSha256.toLatin1());
		if (expected.size() != QCryptographicHash::hashLength(
				QCryptographicHash::Sha256)) {
			return false;
		}
		const auto initial = QFileInfo(path);
		if (!initial.isFile() || initial.size() != expectedSize) {
			return false;
		}
		QFile file(path);
		if (!file.open(QIODevice::ReadOnly)) {
			return false;
		}

		auto hash = QCryptographicHash(QCryptographicHash::Sha256);
		qint64 read = 0;
		while (!file.atEnd()) {
			const auto chunk = file.read(kModelHashChunkSize);
			if (chunk.isEmpty()) {
				if (file.error() != QFileDevice::NoError) {
					return false;
				}
				break;
			}
			if (read > expectedSize - chunk.size()) {
				return false;
			}
			hash.addData(chunk);
			read += chunk.size();
		}
		const auto final = QFileInfo(path);
		return read == expectedSize
			&& final.isFile()
			&& final.size() == expectedSize
			&& hash.result() == expected;
	} catch (...) {
		return false;
	}
}

void DownloadWithProgress(
		const QString &url,
		const QString &destPath,
		const QString &sha256Expected,
		const std::function<void(int percent)> &onProgress,
		const std::function<void(bool ok)> &onDone) {
	DownloadWithProgress(
		url,
		destPath,
		sha256Expected,
		kUnknownExpectedDownloadSize,
		onProgress,
		onDone);
}

void DownloadWithProgress(
		const QString &url,
		const QString &destPath,
		const QString &sha256Expected,
		const qint64 expectedSize,
		const std::function<void(int percent)> &onProgress,
		const std::function<void(bool ok)> &onDone) {
	const auto fail = [&] {
		if (onDone) {
			try {
				onDone(false);
			} catch (...) {
				LOG(("DownloadWithProgress: failure callback failed"));
			}
		}
	};
	try {
		const auto state = new DownloadState(
			url,
			destPath,
			sha256Expected,
			expectedSize,
			onProgress,
			onDone);
		state->start();
	} catch (...) {
		fail();
	}
}

} // namespace Ayu::STT
