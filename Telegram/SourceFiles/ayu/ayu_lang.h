// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

#include <QtCore/QJsonDocument>
#include <QtCore/QPointer>
#include <QtCore/QSet>
#include <QtNetwork/QNetworkAccessManager>
#include <QtNetwork/QNetworkReply>
#include <QtXml/QDomDocument>

#include "rpl/lifetime.h"

class AyuLanguage : public QObject
{
	Q_OBJECT
	Q_DISABLE_COPY(AyuLanguage)

public:
	static AyuLanguage *currentInstance();
	static void init();
	static AyuLanguage *instance;

	void applyLanguageJson(const QJsonDocument &doc);

private:
	AyuLanguage();
	~AyuLanguage() override = default;

	void loadCachedLanguage();
	void syncLanguage();
	void fetchLanguage(
		const QString &id,
		quint64 generation,
		bool mirror = false);
	void saveCachedLanguage(
		const QJsonDocument &document,
		const QString &langId);
	void clearAppliedLanguage();
	[[nodiscard]] QString getCacheDir() const;
	[[nodiscard]] QString getCachePath(const QString &langId) const;

	QNetworkAccessManager networkManager;
	QPointer<QNetworkReply> _chkReply;
	QString _currentLangId;
	QString _baseLangId;
	QJsonDocument _document;
	QSet<QByteArray> _appliedKeys;
	quint64 _generation = 0;
	bool _applying = false;
	rpl::lifetime _lifetime;
};
