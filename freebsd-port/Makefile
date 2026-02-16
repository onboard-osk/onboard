PORTNAME=	onboard
DISTVERSION=	1.4.3.post9
CATEGORIES=	x11 accessibility
MASTER_SITES=	# set by USE_GITHUB

MAINTAINER=	hans@alipang.se
COMMENT=	On-screen keyboard for X11
WWW=	https://github.com/alipang/onboard

LICENSE=	GPLv3+
LICENSE_FILE=	${WRKSRC}/COPYING.GPL3

USE_GITHUB=	yes
GH_ACCOUNT=	alipang
GH_TAGNAME=	03d46576f908f368a0ce8c003e573dad9321105b

BUILD_DEPENDS=	${PYTHON_PKGNAMEPREFIX}python-distutils-extra>0:devel/py-python-distutils-extra@${PY_FLAVOR} \
		${PYTHON_PKGNAMEPREFIX}packaging>0:devel/py-packaging@${PY_FLAVOR} \
		bash:shells/bash \
		intltool-update:textproc/intltool
LIB_DEPENDS=	libhunspell-1.7.so:textproc/hunspell \
		libcanberra.so:audio/libcanberra \
		libdconf.so:devel/dconf \
		libudev.so:devel/libudev-devd
RUN_DEPENDS=	${PYTHON_PKGNAMEPREFIX}dbus>0:devel/py-dbus@${PY_FLAVOR} \
		${PYTHON_PKGNAMEPREFIX}cairo>0:graphics/py-cairo@${PY_FLAVOR} \
		gsettings-desktop-schemas>0:devel/gsettings-desktop-schemas \
		at-spi2-core>0:accessibility/at-spi2-core \
		iso-codes>0:misc/iso-codes \
		bash:shells/bash

USES=		compiler:c++11-lang gettext gnome localbase pkgconfig python \
		desktop-file-utils xorg
USE_GNOME=	cairo gtk30 pygobject3
USE_XORG=	x11 xi xtst xkbfile
USE_PYTHON=	distutils autoplist

GLIB_SCHEMAS=	org.onboard.gschema.xml

# distutils-extra handles i18n, icons, desktop files
INSTALL_TARGET=	install

# Prevent setup.py from trying to run /bin/bash find commands on FreeBSD
# where /bin/bash may not exist yet during build
MAKE_ENV+=	SHELL=/bin/sh

post-patch:
	@${REINPLACE_CMD} -e 's|#!/usr/bin/python3|#!/usr/bin/env python3|' \
		${WRKSRC}/onboard ${WRKSRC}/onboard-settings

post-install:
	@${FIND} ${STAGEDIR}${PYTHON_SITELIBDIR}/Onboard -name '*.so' -exec ${STRIP_CMD} {} +

.include <bsd.port.mk>
