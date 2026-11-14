package com.android.vsteam;

import static com.android.vsteam.Toolbox.vslog;

import android.os.Build;
import android.security.keystore.KeyProperties;
import android.util.Log;

import org.lsposed.lsparanoid.Obfuscate;
import org.spongycastle.asn1.ASN1Boolean;
import org.spongycastle.asn1.ASN1Encodable;
import org.spongycastle.asn1.ASN1EncodableVector;
import org.spongycastle.asn1.ASN1Enumerated;
import org.spongycastle.asn1.ASN1Integer;
import org.spongycastle.asn1.ASN1ObjectIdentifier;
import org.spongycastle.asn1.ASN1OctetString;
import org.spongycastle.asn1.ASN1Sequence;
import org.spongycastle.asn1.ASN1TaggedObject;
import org.spongycastle.asn1.DEROctetString;
import org.spongycastle.asn1.DERNull;
import org.spongycastle.asn1.DERSequence;
import org.spongycastle.asn1.DERSet;
import org.spongycastle.asn1.DERTaggedObject;
import org.spongycastle.asn1.pkcs.PrivateKeyInfo;
import org.spongycastle.asn1.x500.X500Name;
import org.spongycastle.asn1.x509.Extension;
import org.spongycastle.asn1.x509.KeyUsage;
import org.spongycastle.asn1.x509.SubjectPublicKeyInfo;
import org.spongycastle.cert.X509CertificateHolder;
import org.spongycastle.cert.X509v3CertificateBuilder;
import org.spongycastle.cert.jcajce.JcaX509CertificateConverter;
import org.spongycastle.openssl.PEMKeyPair;
import org.spongycastle.openssl.PEMParser;
import org.spongycastle.operator.ContentSigner;
import org.spongycastle.operator.jcajce.JcaContentSignerBuilder;
import org.spongycastle.util.io.pem.PemReader;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.math.BigInteger;
import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.interfaces.ECKey;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Date;
import java.util.LinkedList;
import java.util.List;
import java.lang.reflect.Method;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.XMLConstants;

@Obfuscate
public final class SubAttest {
    public static final String TAG = "VSLog";
    public static final List<Certificate> EC_CERTS = new ArrayList<>();
    public static final List<Certificate> RSA_CERTS = new ArrayList<>();
    public static final ASN1ObjectIdentifier OID = new ASN1ObjectIdentifier("1.3.6.1.4.1.11129.2.1.17");
    public static PEMKeyPair EC;
    public static PEMKeyPair RSA;
    public static PrivateKey EC_P;
    public static PrivateKey RSA_P;
    public static CertificateFactory certificateFactory;
    public static volatile String algo;
    private static X509CertificateHolder EC_holder;
    private static X509CertificateHolder RSA_holder;
    private static final Object KEYBOX_LOCK = new Object();

    /**
     * Current RSA Google Hardware Attestation Root published by Android.
     *
     * <p>The factory keybox bundled with older devices contains the previous certificate for the
     * same root public key, which expired on 2026-05-24. Some verifiers match the complete trust
     * anchor certificate instead of only its public key, so replace that old certificate while
     * loading the keybox. The intermediate signatures remain valid because the root key is
     * unchanged.</p>
     */
    private static final String CURRENT_GOOGLE_HARDWARE_ATTESTATION_ROOT = """
            -----BEGIN CERTIFICATE-----
            MIIFHDCCAwSgAwIBAgIJAPHBcqaZ6vUdMA0GCSqGSIb3DQEBCwUAMBsxGTAXBgNV
            BAUTEGY5MjAwOWU4NTNiNmIwNDUwHhcNMjIwMzIwMTgwNzQ4WhcNNDIwMzE1MTgw
            NzQ4WjAbMRkwFwYDVQQFExBmOTIwMDllODUzYjZiMDQ1MIICIjANBgkqhkiG9w0B
            AQEFAAOCAg8AMIICCgKCAgEAr7bHgiuxpwHsK7Qui8xUFmOr75gvMsd/dTEDDJdS
            Sxtf6An7xyqpRR90PL2abxM1dEqlXnf2tqw1Ne4Xwl5jlRfdnJLmN0pTy/4lj4/7
            tv0Sk3iiKkypnEUtR6WfMgH0QZfKHM1+di+y9TFRtv6y//0rb+T+W8a9nsNL/ggj
            nar86461qO0rOs2cXjp3kOG1FEJ5MVmFmBGtnrKpa73XpXyTqRxB/M0n1n/W9nGq
            C4FSYa04T6N5RIZGBN2z2MT5IKGbFlbC8UrW0DxW7AYImQQcHtGl/m00QLVWutHQ
            oVJYnFPlXTcHYvASLu+RhhsbDmxMgJJ0mcDpvsC4PjvB+TxywElgS70vE0XmLD+O
            JtvsBslHZvPBKCOdT0MS+tgSOIfga+z1Z1g7+DVagf7quvmag8jfPioyKvxnK/Eg
            sTUVi2ghzq8wm27ud/mIM7AY2qEORR8Go3TVB4HzWQgpZrt3i5MIlCaY504LzSRi
            igHCzAPlHws+W0rB5N+er5/2pJKnfBSDiCiFAVtCLOZ7gLiMm0jhO2B6tUXHI/+M
            RPjy02i59lINMRRev56GKtcd9qO/0kUJWdZTdA2XoS82ixPvZtXQpUpuL12ab+9E
            aDK8Z4RHJYYfCT3Q5vNAXaiWQ+8PTWm2QgBR/bkwSWc+NpUFgNPN9PvQi8WEg5Um
            AGMCAwEAAaNjMGEwHQYDVR0OBBYEFDZh4QB8iAUJUYtEbEf/GkzJ6k8SMB8GA1Ud
            IwQYMBaAFDZh4QB8iAUJUYtEbEf/GkzJ6k8SMA8GA1UdEwEB/wQFMAMBAf8wDgYD
            VR0PAQH/BAQDAgIEMA0GCSqGSIb3DQEBCwUAA4ICAQB8cMqTllHc8U+qCrOlg3H7
            174lmaCsbo/bJ0C17JEgMLb4kvrqsXZs01U3mB/qABg/1t5Pd5AORHARs1hhqGIC
            W/nKMav574f9rZN4PC2ZlufGXb7sIdJpGiO9ctRhiLuYuly10JccUZGEHpHSYM2G
            tkgYbZba6lsCPYAAP83cyDV+1aOkTf1RCp/lM0PKvmxYN10RYsK631jrleGdcdkx
            oSK//mSQbgcWnmAEZrzHoF1/0gso1HZgIn0YLzVhLSA/iXCX4QT2h3J5z3znluKG
            1nv8NQdxei2DIIhASWfu804CA96cQKTTlaae2fweqXjdN1/v2nqOhngNyz1361mF
            mr4XmaKH/ItTwOe72NI9ZcwS1lVaCvsIkTDCEXdm9rCNPAY10iTunIHFXRh+7KPz
            lHGewCq/8TOohBRn0/NNfh7uRslOSZ/xKbN9tMBtw37Z8d2vvnXq/YWdsm1+JLVw
            n6yYD/yacNJBlwpddla8eaVMjsF6nBnIgQOf9zKSe06nSTqvgwUHosgOECZJZ1Eu
            zbH4yswbt02tKtKEFhx+v+OTge/06V+jGsqTWLsfrOCNLuA8H++z+pUENmpqnnHo
            vaI47gC+TNpkgYGkkBT6B/m/U01BuOBBTzhIlMEZq9qkDWuM2cA5kW5V3FJUcfHn
            w1IdYIg2Wxg7yHcQZemFQg==
            -----END CERTIFICATE-----
            """;

    /** VerifiedBootState per Android key attestation schema (1.3.6.1.4.1.11129.2.1.17). */
    private static final int VERIFIED_BOOT_VERIFIED = 0;     // Full chain verified (OEM)

    /** AuthorizationList tags this backend knows how to override. */
    public static final int TAG_ROOT_OF_TRUST = 704;
    public static final int TAG_OS_VERSION = 705;
    public static final int TAG_OS_PATCH_LEVEL = 706;
    public static final int TAG_VENDOR_PATCH_LEVEL = 718;
    public static final int TAG_BOOT_PATCH_LEVEL = 719;
    /** attestationIdSerial: only overridden when a per-app custom config supplies an explicit value. */
    public static final int TAG_ATTESTATION_ID_SERIAL = 713;

    /** Default: patch everything, as the original implementation always did. */
    private static final Set<Integer> ALL_ATTESTATION_TAGS = Set.of(
            TAG_ROOT_OF_TRUST, TAG_OS_VERSION, TAG_OS_PATCH_LEVEL, TAG_VENDOR_PATCH_LEVEL, TAG_BOOT_PATCH_LEVEL);

    private static final String PROP_VBMETA_DIGEST = "ro.boot.vbmeta.digest";
    private static final String PROP_VBMETA_PUBLIC_KEY_DIGEST =
            "ro.boot.vbmeta.public_key_digest";

    /** Same Settings.Global prefix VsProps uses to spoof Build fields for GMS/Play Integrity. */
    private static final String PI_PROP_PREFIX = "vs_pi_";

    private static String getPiSetting(String fieldName) {
        try {
            String value = Toolbox.getStringGlobal(PI_PROP_PREFIX + fieldName);
            return (value == null || value.trim().isEmpty()) ? null : value.trim();
        } catch (Throwable t) {
            return null;
        }
    }

    private static final Method SYSTEM_PROPERTY_GET = findSystemPropertyGetMethod();

    private static final SecureRandom SECURE_RANDOM = new SecureRandom();
    /** Process-stable fallbacks used only when the source attestation has no usable RootOfTrust. */
    private static final byte[] FALLBACK_VERIFIED_BOOT_KEY = new byte[32];
    private static final byte[] FIXED_VERIFIED_BOOT_HASH = new byte[32];

    static {
        SECURE_RANDOM.nextBytes(FALLBACK_VERIFIED_BOOT_KEY);
        SECURE_RANDOM.nextBytes(FIXED_VERIFIED_BOOT_HASH);
    }

    private static Method findSystemPropertyGetMethod() {
        try {
            Class<?> c = Class.forName("android.os.SystemProperties");
            return c.getMethod("get", String.class, String.class);
        } catch (Throwable t) {
            return null;
        }
    }

    private static String getSystemProperty(String key, String def) {
        if (SYSTEM_PROPERTY_GET == null) return def;
        try {
            return (String) SYSTEM_PROPERTY_GET.invoke(null, key, def);
        } catch (Throwable t) {
            return def;
        }
    }

    /** Decode hex string → 32 byte. Chấp nhận 64 hex char (SHA-256) hoặc 128 (SHA-512, lấy 32 byte đầu). */
    private static byte[] hexStringToBytes(String hex) {
        if (hex == null) return null;
        String s = hex.trim();
        if (s.length() != 64 && s.length() != 128) return null;
        byte[] out = new byte[32];
        for (int i = 0; i < 32; i++) {
            int high = Character.digit(s.charAt(i * 2), 16);
            int low = Character.digit(s.charAt(i * 2 + 1), 16);
            if (high < 0 || low < 0) return null;
            out[i] = (byte) ((high << 4) | low);
        }
        return out;
    }

    /**
     * OS version: 6-digit integer (e.g. 8.1.0 → 080100).
     * Prefers the {@code vs_pi_RELEASE} override (same value VsProps spoofs into
     * Build.VERSION.RELEASE for GMS/Play Integrity) so the attestation matches what
     * Play Integrity is told, falling back to the real Build.VERSION.RELEASE.
     */
    private static int getOsVersionLevelVal() {
        String release = getPiSetting("RELEASE");
        if (release == null) release = Build.VERSION.RELEASE;
        if (release == null || release.isEmpty()) return 140000;
        String[] parts = release.split("\\.");
        int major = parseIntSafe(parts.length > 0 ? parts[0] : "14", 14);
        int minor = parts.length > 1 ? parseIntSafe(parts[1], 0) : 0;
        int patch = parts.length > 2 ? parseIntSafe(parts[2], 0) : 0;
        return major * 10000 + minor * 100 + patch;
    }

    private static int parseIntSafe(String s, int def) {
        try {
            return Integer.parseInt(s.trim());
        } catch (NumberFormatException e) {
            return def;
        }
    }

    /**
     * Security patch: YYYYMM from Build.VERSION.SECURITY_PATCH (e.g. 2024-06-01 → 202406).
     * Prefers the {@code vs_pi_SECURITY_PATCH} override for the same reason as
     * {@link #getOsVersionLevelVal()}.
     */
    private static int getOsPatchLevelVal() {
        String patch = getPiSetting("SECURITY_PATCH");
        if (patch == null) patch = Build.VERSION.SECURITY_PATCH;
        if (patch == null || !patch.matches("\\d{4}-\\d{2}-\\d{2}")) return 202406;
        String[] parts = patch.split("-");
        return Integer.parseInt(parts[0] + parts[1]);
    }

    /** Vendor/Boot patch: YYYYMMDD (first day of patch month) per attestation schema. */
    private static int getVendorBootPatchLevelVal() {
        return Integer.parseInt(String.valueOf(getOsPatchLevelVal()).concat("01"));
    }

    /** Build RootOfTrust SEQUENCE per schema: verifiedBootKey, deviceLocked, verifiedBootState, verifiedBootHash. */
    private static ASN1Sequence buildRootOfTrust(byte[] verifiedBootKey, byte[] verifiedBootHash, boolean deviceLocked, int verifiedBootState) {
        ASN1Encodable[] enc = {
                new DEROctetString(verifiedBootKey),
                deviceLocked ? ASN1Boolean.TRUE : ASN1Boolean.FALSE,
                new ASN1Enumerated(verifiedBootState),
                new DEROctetString(verifiedBootHash)
        };
        return new DERSequence(enc);
    }

    /** Preserve all known and future tags while emitting AuthorizationList in numeric tag order. */
    private static ASN1Sequence buildTeeEnforcedCanonical(ASN1Sequence teeEnforced, Map<Integer, ASN1TaggedObject> overrides) {
        Map<Integer, ASN1TaggedObject> byTag = new TreeMap<>();
        for (ASN1Encodable e : teeEnforced) {
            ASN1TaggedObject t = (ASN1TaggedObject) e;
            byTag.put(t.getTagNo(), t);
        }
        byTag.putAll(overrides);
        ASN1EncodableVector v = new ASN1EncodableVector();
        for (ASN1TaggedObject obj : byTag.values()) {
            v.add(obj);
        }
        return new DERSequence(v);
    }

    private static ASN1Encodable findRootOfTrust(ASN1Sequence teeEnforced) {
        for (ASN1Encodable element : teeEnforced) {
            ASN1TaggedObject taggedObject = (ASN1TaggedObject) element;
            if (taggedObject.getTagNo() == 704) {
                return taggedObject.getObject();
            }
        }
        return null;
    }

    /** @param overrideHex 32-byte hex string that wins over every other source, or null/invalid to fall through. */
    private static byte[] resolveVerifiedBootKey(ASN1Encodable originalRootOfTrust, String overrideHex) {
        byte[] override = hexStringToBytes(overrideHex);
        if (override != null) {
            return override;
        }

        byte[] bootKeyDigest = hexStringToBytes(
                getSystemProperty(PROP_VBMETA_PUBLIC_KEY_DIGEST, ""));
        if (bootKeyDigest != null) {
            return bootKeyDigest;
        }

        if (originalRootOfTrust instanceof ASN1Sequence rot && rot.size() > 0) {
            try {
                byte[] bytes = ASN1OctetString.getInstance(rot.getObjectAt(0)).getOctets();
                if (bytes != null && bytes.length > 0) return bytes;
            } catch (Throwable ignored) {
            }
        }
        return FALLBACK_VERIFIED_BOOT_KEY.clone();
    }

    /** @param overrideHex 32-byte hex string that wins over every other source, or null/invalid to fall through. */
    private static byte[] resolveVerifiedBootHash(ASN1Encodable originalRootOfTrust, String overrideHex) {
        byte[] override = hexStringToBytes(overrideHex);
        if (override != null) {
            return override;
        }

        String vbmetaDigest = getSystemProperty(PROP_VBMETA_DIGEST, "");
        if (vbmetaDigest != null && !vbmetaDigest.isEmpty()) {
            byte[] decoded = hexStringToBytes(vbmetaDigest);
            if (decoded != null && decoded.length == 32) {
                return decoded;
            }
        }

        if (originalRootOfTrust instanceof ASN1Sequence rot && rot.size() > 3) {
            try {
                ASN1OctetString bootHash = (ASN1OctetString) rot.getObjectAt(3);
                if (bootHash != null) {
                    byte[] bytes = bootHash.getOctets();
                    if (bytes != null && bytes.length > 0) {
                        return bytes;
                    }
                }
            } catch (Throwable ignored) {
            }
        }

        return FIXED_VERIFIED_BOOT_HASH.clone();
    }

    /**
     * @param tags which AuthorizationList tags to override (see TAG_* constants); any tag not in
     *             this set is left exactly as it was in the source attestation. Backs the
     *             "custom" mode driven by the per-app {@code vs_custom_keyattest_<pkg>} JSON.
     */
    private static Extension hackAttestationExtension(ASN1Encodable originalRootOfTrust, ASN1Sequence teeEnforced, ASN1Encodable[] originalEncodables, Set<Integer> tags) throws Exception {
        return hackAttestationExtension(originalRootOfTrust, teeEnforced, originalEncodables, tags, null, null, null);
    }

    /**
     * @param tags                    which AuthorizationList tags to override (see TAG_* constants).
     * @param verifiedBootKeyHex      32-byte hex override for RootOfTrust.verifiedBootKey, or
     *                                null to fall back to the system property / original value.
     * @param verifiedBootHashHex     32-byte hex override for RootOfTrust.verifiedBootHash, same
     *                                fallback rule. Both are only used when tag 704 is in {@code tags}.
     * @param attestationIdSerial     explicit value for attestationIdSerial, or null/blank to leave
     *                                the original attestation's value untouched. Only used when
     *                                {@link #TAG_ATTESTATION_ID_SERIAL} is in {@code tags}.
     */
    private static Extension hackAttestationExtension(ASN1Encodable originalRootOfTrust, ASN1Sequence teeEnforced, ASN1Encodable[] originalEncodables, Set<Integer> tags, String verifiedBootKeyHex, String verifiedBootHashHex, String attestationIdSerial) throws Exception {
        Map<Integer, ASN1TaggedObject> overrides = new TreeMap<>();
        if (tags.contains(TAG_ROOT_OF_TRUST)) {
            byte[] verifiedBootKey = resolveVerifiedBootKey(originalRootOfTrust, verifiedBootKeyHex);
            byte[] verifiedBootHash = resolveVerifiedBootHash(originalRootOfTrust, verifiedBootHashHex);
            overrides.put(TAG_ROOT_OF_TRUST, new DERTaggedObject(true, TAG_ROOT_OF_TRUST, buildRootOfTrust(
                    verifiedBootKey,
                    verifiedBootHash,
                    true,
                    VERIFIED_BOOT_VERIFIED)));
        }
        if (tags.contains(TAG_OS_VERSION)) {
            overrides.put(TAG_OS_VERSION, new DERTaggedObject(true, TAG_OS_VERSION, new ASN1Integer(getOsVersionLevelVal())));
        }
        if (tags.contains(TAG_OS_PATCH_LEVEL)) {
            overrides.put(TAG_OS_PATCH_LEVEL, new DERTaggedObject(true, TAG_OS_PATCH_LEVEL, new ASN1Integer(getOsPatchLevelVal())));
        }
        if (tags.contains(TAG_VENDOR_PATCH_LEVEL)) {
            overrides.put(TAG_VENDOR_PATCH_LEVEL, new DERTaggedObject(true, TAG_VENDOR_PATCH_LEVEL, new ASN1Integer(getVendorBootPatchLevelVal())));
        }
        if (tags.contains(TAG_BOOT_PATCH_LEVEL)) {
            overrides.put(TAG_BOOT_PATCH_LEVEL, new DERTaggedObject(true, TAG_BOOT_PATCH_LEVEL, new ASN1Integer(getVendorBootPatchLevelVal())));
        }
        if (tags.contains(TAG_ATTESTATION_ID_SERIAL) && attestationIdSerial != null && !attestationIdSerial.isEmpty()) {
            overrides.put(TAG_ATTESTATION_ID_SERIAL, new DERTaggedObject(true, TAG_ATTESTATION_ID_SERIAL,
                    new DEROctetString(attestationIdSerial.getBytes(StandardCharsets.UTF_8))));
        }

        originalEncodables[7] = buildTeeEnforcedCanonical(teeEnforced, overrides);
        return new Extension(OID, false, new DEROctetString(new DERSequence(originalEncodables)));
    }

    private static SubjectPublicKeyInfo getSelectedSubjectPublicKeyInfo_Stock(String keyAlgorithm, X509CertificateHolder leafHolder) {
        return leafHolder.getSubjectPublicKeyInfo();
    }

    private static SubjectPublicKeyInfo getSelectedSubjectPublicKeyInfo_Keybox(String keyAlgorithm, X509CertificateHolder leafHolder) {
        if (KeyProperties.KEY_ALGORITHM_EC.equals(keyAlgorithm)) {
            return EC.getPublicKeyInfo();
        }
        if (KeyProperties.KEY_ALGORITHM_RSA.equals(keyAlgorithm)) {
            return RSA.getPublicKeyInfo();
        }
        return leafHolder.getSubjectPublicKeyInfo();
    }

    static {
        initializeKeybox();
    }

    private static void initializeKeybox() {
        String customKeybox = null;
        try {
            customKeybox = Toolbox.getStringGlobal("vs_keybox_content");
        } catch (Throwable t) {
            Log.e(TAG, "Failed to read custom keybox settings; using bundled keybox", t);
        }

        if (customKeybox != null && !customKeybox.trim().isEmpty()) {
            try {
                parseXMLKeybox(customKeybox);
                return;
            } catch (Throwable t) {
                Log.e(TAG, "Failed to load custom keybox; using bundled keybox", t);
            }
        }

        try {
            parseXMLKeybox(DefaultKeybox.keybox);
        } catch (Throwable t) {
            Log.e(TAG, "Failed to load bundled keybox", t);
        }
    }


    public static void parseXMLKeybox(String xmlContent) throws Throwable {
        if (xmlContent == null) {
            throw new IllegalArgumentException("Keybox XML is null");
        }
        String upperXml = xmlContent.toUpperCase(Locale.ROOT);
        if (upperXml.contains("<!DOCTYPE") || upperXml.contains("<!ENTITY")) {
            throw new IllegalArgumentException("Keybox XML must not contain DTD or entity declarations");
        }

        certificateFactory = CertificateFactory.getInstance("X.509");

        DocumentBuilderFactory dbFactory = DocumentBuilderFactory.newInstance();
        setParserFeatureIfSupported(dbFactory, XMLConstants.FEATURE_SECURE_PROCESSING, true);
        setParserFeatureIfSupported(dbFactory, "http://apache.org/xml/features/disallow-doctype-decl", true);
        setParserFeatureIfSupported(dbFactory, "http://xml.org/sax/features/external-general-entities", false);
        setParserFeatureIfSupported(dbFactory, "http://xml.org/sax/features/external-parameter-entities", false);
        setParserFeatureIfSupported(dbFactory, "http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
        try {
            dbFactory.setXIncludeAware(false);
        } catch (Throwable ignored) {
        }
        try {
            dbFactory.setExpandEntityReferences(false);
        } catch (Throwable ignored) {
        }
        DocumentBuilder dBuilder = dbFactory.newDocumentBuilder();
        Document doc = dBuilder.parse(new ByteArrayInputStream(xmlContent.getBytes(StandardCharsets.UTF_8)));

        PEMKeyPair parsedEc = null;
        PEMKeyPair parsedRsa = null;
        PrivateKey parsedEcPrivate = null;
        PrivateKey parsedRsaPrivate = null;
        List<Certificate> parsedEcCerts = null;
        List<Certificate> parsedRsaCerts = null;

        NodeList keyboxList = doc.getElementsByTagName("Keybox");
        for (int i = 0; i < keyboxList.getLength(); i++) {
            Node keyboxNode = keyboxList.item(i);
            if (keyboxNode.getNodeType() == Node.ELEMENT_NODE) {
                Element keyboxElement = (Element) keyboxNode;

                NodeList keys = keyboxElement.getElementsByTagName("Key");
                for (int j = 0; j < keys.getLength(); j++) {
                    Element keyElement = (Element) keys.item(j);
                    String algorithm = keyElement.getAttribute("algorithm");

                    String privateKey = keyElement.getElementsByTagName("PrivateKey").item(0).getTextContent().trim();
                    PEMKeyPair keyPair = parseKeyPair(privateKey);

                    NodeList certNodes = keyElement.getElementsByTagName("Certificate");
                    List<Certificate> certs = new ArrayList<>();
                    for (int k = 0; k < certNodes.getLength(); k++) {
                        String certRaw = certNodes.item(k).getTextContent().trim();
                        certs.add(parseCert(certRaw));
                    }

                    if ("ecdsa".equalsIgnoreCase(algorithm)) {
                        parsedEc = keyPair;
                        parsedEcCerts = certs;
                        parsedEcPrivate = parsePrivateKeyFromPEMKeyPair(privateKey, KeyProperties.KEY_ALGORITHM_EC);
                    } else if ("rsa".equalsIgnoreCase(algorithm)) {
                        parsedRsa = keyPair;
                        parsedRsaCerts = certs;
                        parsedRsaPrivate = parsePrivateKeyFromPEMKeyPair(privateKey, KeyProperties.KEY_ALGORITHM_RSA);
                    }
                }
            }
        }

        validateKeyboxEntry(KeyProperties.KEY_ALGORITHM_EC, parsedEc, parsedEcPrivate, parsedEcCerts);
        validateKeyboxEntry(KeyProperties.KEY_ALGORITHM_RSA, parsedRsa, parsedRsaPrivate, parsedRsaCerts);

        upgradeGoogleHardwareAttestationRoot(parsedEcCerts);
        upgradeGoogleHardwareAttestationRoot(parsedRsaCerts);

        X509CertificateHolder parsedEcHolder = new X509CertificateHolder(parsedEcCerts.get(0).getEncoded());
        X509CertificateHolder parsedRsaHolder = new X509CertificateHolder(parsedRsaCerts.get(0).getEncoded());
        synchronized (KEYBOX_LOCK) {
            EC = parsedEc;
            RSA = parsedRsa;
            EC_P = parsedEcPrivate;
            RSA_P = parsedRsaPrivate;
            EC_holder = parsedEcHolder;
            RSA_holder = parsedRsaHolder;
            EC_CERTS.clear();
            EC_CERTS.addAll(parsedEcCerts);
            RSA_CERTS.clear();
            RSA_CERTS.addAll(parsedRsaCerts);
        }
    }

    private static void setParserFeatureIfSupported(DocumentBuilderFactory factory,
                                                     String feature, boolean enabled) {
        try {
            factory.setFeature(feature, enabled);
        } catch (Throwable ignored) {
            // Android's built-in XML provider does not implement every JAXP feature.
        }
    }

    private static void validateKeyboxEntry(String algorithm, PEMKeyPair keyPair,
                                            PrivateKey privateKey, List<Certificate> certificates) throws Exception {
        if (keyPair == null || privateKey == null || certificates == null || certificates.isEmpty()) {
            throw new IllegalArgumentException("Missing " + algorithm + " keybox entry");
        }

        X509Certificate leaf = toX509Certificate(certificates.get(0));
        if (!Arrays.equals(keyPair.getPublicKeyInfo().getEncoded(), leaf.getPublicKey().getEncoded())) {
            throw new IllegalArgumentException(algorithm + " keybox public key does not match its certificate");
        }

        Signature signature = Signature.getInstance(defaultSignatureAlgorithm(privateKey));
        byte[] probe = "VS keybox validation".getBytes(StandardCharsets.UTF_8);
        signature.initSign(privateKey);
        signature.update(probe);
        byte[] signed = signature.sign();
        signature.initVerify(leaf.getPublicKey());
        signature.update(probe);
        if (!signature.verify(signed)) {
            throw new IllegalArgumentException(algorithm + " keybox private key does not match its certificate");
        }
    }

    private static void upgradeGoogleHardwareAttestationRoot(List<Certificate> certificates)
            throws Exception {
        if (certificates == null || certificates.isEmpty()) return;

        X509Certificate currentRoot = (X509Certificate) parseCert(
                CURRENT_GOOGLE_HARDWARE_ATTESTATION_ROOT);
        int rootIndex = certificates.size() - 1;
        X509Certificate keyboxRoot = toX509Certificate(certificates.get(rootIndex));
        if (Arrays.equals(keyboxRoot.getPublicKey().getEncoded(),
                currentRoot.getPublicKey().getEncoded())
                && !Arrays.equals(keyboxRoot.getEncoded(), currentRoot.getEncoded())) {
            certificates.set(rootIndex, currentRoot);
            vslog("Upgraded Google Hardware Attestation Root certificate");
        }
    }


    public static PrivateKey parsePrivateKeyFromPEMKeyPair(String pemKeyPair, String algo) throws Exception {
        try (PEMParser parser = new PEMParser(new StringReader(pemKeyPair))) {
            Object object = parser.readObject();
            if (object instanceof PEMKeyPair keyPair) {

                PrivateKeyInfo privateKeyInfo = keyPair.getPrivateKeyInfo();

                byte[] keyBytes = privateKeyInfo.getEncoded();
                String base64Key = Base64.getEncoder().encodeToString(keyBytes);

                return recreatePrivateKey(base64Key, algo);
            }
            throw new IllegalArgumentException("Invalid PEM KeyPair format");
        }
    }

    private static PrivateKey recreatePrivateKey(String base64Key, String algo) throws Exception {
        byte[] keyBytes = Base64.getDecoder().decode(base64Key);

        PKCS8EncodedKeySpec spec = new PKCS8EncodedKeySpec(keyBytes);

        return KeyFactory.getInstance(algo).generatePrivate(spec);
    }


    public static PEMKeyPair parseKeyPair(String key) throws Exception {
        try (PEMParser parser = new PEMParser(new StringReader(key))) {
            return (PEMKeyPair) parser.readObject();
        }
    }

    public static Certificate parseCert(String cert) throws Exception {
        try (PemReader reader = new PemReader(new StringReader(cert))) {
            return certificateFactory.generateCertificate(new ByteArrayInputStream(reader.readPemObject().getContent()));
        }
    }

    private static final class ParsedAttestation {
        final X509Certificate leaf;
        final X509CertificateHolder holder;
        final ASN1Encodable[] encodables;
        final ASN1Sequence teeEnforced;
        final ASN1Encodable originalRootOfTrust;
        final String keyAlgorithm;
        final boolean isEc;

        ParsedAttestation(X509Certificate leaf, X509CertificateHolder holder, ASN1Encodable[] encodables,
                          ASN1Sequence teeEnforced, ASN1Encodable originalRootOfTrust) {
            this.leaf = leaf;
            this.holder = holder;
            this.encodables = encodables;
            this.teeEnforced = teeEnforced;
            this.originalRootOfTrust = originalRootOfTrust;
            this.keyAlgorithm = leaf.getPublicKey().getAlgorithm();
            this.isEc = KeyProperties.KEY_ALGORITHM_EC.equals(this.keyAlgorithm);
        }
    }

    private static final class SigningMaterial {
        final List<Certificate> certificates;
        final X509CertificateHolder issuerHolder;
        final PrivateKey privateKey;
        final SubjectPublicKeyInfo subjectPublicKeyInfo;

        SigningMaterial(List<Certificate> certificates, X509CertificateHolder issuerHolder,
                        PrivateKey privateKey, SubjectPublicKeyInfo subjectPublicKeyInfo) {
            this.certificates = certificates;
            this.issuerHolder = issuerHolder;
            this.privateKey = privateKey;
            this.subjectPublicKeyInfo = subjectPublicKeyInfo;
        }
    }

    private static X509Certificate toX509Certificate(Certificate certificate) throws Exception {
        if (certificate instanceof X509Certificate) {
            return (X509Certificate) certificate;
        }
        return (X509Certificate) certificateFactory.generateCertificate(
                new ByteArrayInputStream(certificate.getEncoded()));
    }

    private static ParsedAttestation parseAttestation(X509Certificate leaf, byte[] certBytes) throws Throwable {
        if (leaf.getExtensionValue(OID.getId()) == null) throw new Exception();

        X509CertificateHolder holder = new X509CertificateHolder(certBytes);
        Extension extension = holder.getExtension(OID);
        ASN1Sequence sequence = ASN1Sequence.getInstance(extension.getExtnValue().getOctets());
        ASN1Encodable[] encodables = sequence.toArray();
        ASN1Sequence teeEnforced = (ASN1Sequence) encodables[7];
        ASN1Encodable rootOfTrust = findRootOfTrust(teeEnforced);

        return new ParsedAttestation(leaf, holder, encodables, teeEnforced, rootOfTrust);
    }

    private static ParsedAttestation parseAttestation(byte[] certBytes) throws Throwable {
        X509Certificate leaf = (X509Certificate) certificateFactory.generateCertificate(
                new ByteArrayInputStream(certBytes));
        return parseAttestation(leaf, certBytes);
    }

    private static SigningMaterial getSigningMaterial(ParsedAttestation parsed, boolean useKeyboxKeyInfo) {
        synchronized (KEYBOX_LOCK) {
            List<Certificate> certificates = new ArrayList<>(parsed.isEc ? EC_CERTS : RSA_CERTS);
            X509CertificateHolder issuerHolder = parsed.isEc ? EC_holder : RSA_holder;
            PrivateKey privateKey = parsed.isEc ? EC_P : RSA_P;
            if (certificates.isEmpty() || issuerHolder == null || privateKey == null) {
                throw new IllegalStateException("No " + parsed.keyAlgorithm + " signing material loaded");
            }
            SubjectPublicKeyInfo subjectPublicKeyInfo = useKeyboxKeyInfo
                    ? getSelectedSubjectPublicKeyInfo_Keybox(parsed.keyAlgorithm, parsed.holder)
                    : getSelectedSubjectPublicKeyInfo_Stock(parsed.keyAlgorithm, parsed.holder);
            return new SigningMaterial(certificates, issuerHolder, privateKey, subjectPublicKeyInfo);
        }
    }

    private static String defaultSignatureAlgorithm(PrivateKey privateKey) {
        String algorithm = privateKey.getAlgorithm().toUpperCase(Locale.ROOT);
        if (algorithm.equals(KeyProperties.KEY_ALGORITHM_EC)) return ecSignatureAlgorithmForCurve(privateKey);
        if (algorithm.equals(KeyProperties.KEY_ALGORITHM_RSA)) return "SHA256withRSA";
        throw new IllegalArgumentException("Unsupported signing key algorithm: " + privateKey.getAlgorithm());
    }

    /**
     * Matches the signature digest to the EC curve's field size, the way Google issues real
     * hardware-attestation certs (P-256 -> SHA256, P-384 -> SHA384, P-521 -> SHA512). Falls back
     * to SHA256 if the key doesn't expose curve params for some reason.
     */
    private static String ecSignatureAlgorithmForCurve(PrivateKey privateKey) {
        int fieldSizeBits = 256;
        if (privateKey instanceof ECKey) {
            fieldSizeBits = ((ECKey) privateKey).getParams().getCurve().getField().getFieldSize();
        }
        if (fieldSizeBits <= 256) return "SHA256withECDSA";
        if (fieldSizeBits <= 384) return "SHA384withECDSA";
        return "SHA512withECDSA";
    }

    private static String resolveSignatureAlgorithm(String preferred, PrivateKey privateKey) {
        String keyAlgorithm = privateKey.getAlgorithm().toUpperCase(Locale.ROOT);
        if (keyAlgorithm.equals(KeyProperties.KEY_ALGORITHM_EC)) {
            // The original leaf's SigAlgName was chosen for the device's own EC key, which may
            // sit on a different curve than the keybox's signing key — always re-derive the
            // digest from the actual signing key instead of trusting that string.
            return ecSignatureAlgorithmForCurve(privateKey);
        }
        String preferredUpper = preferred == null ? "" : preferred.toUpperCase(Locale.ROOT);
        if (keyAlgorithm.equals(KeyProperties.KEY_ALGORITHM_RSA) && preferredUpper.contains("RSA")) {
            return preferred;
        }
        return defaultSignatureAlgorithm(privateKey);
    }

    private static Extension buildAttestationExtension(ParsedAttestation parsed, Set<Integer> tags) throws Exception {
        return hackAttestationExtension(parsed.originalRootOfTrust, parsed.teeEnforced, parsed.encodables,
                tags == null ? ALL_ATTESTATION_TAGS : tags);
    }

    private static Extension buildAttestationExtension(ParsedAttestation parsed, Set<Integer> tags,
                                                         String verifiedBootKeyHex, String verifiedBootHashHex,
                                                         String attestationIdSerial) throws Exception {
        return hackAttestationExtension(parsed.originalRootOfTrust, parsed.teeEnforced, parsed.encodables,
                tags == null ? ALL_ATTESTATION_TAGS : tags, verifiedBootKeyHex, verifiedBootHashHex, attestationIdSerial);
    }

    private static void copyNonAttestationExtensions(X509v3CertificateBuilder builder, X509CertificateHolder holder) throws Exception {
        for (ASN1ObjectIdentifier extensionOID : holder.getExtensions().getExtensionOIDs()) {
            if (OID.getId().equals(extensionOID.getId())) continue;
            builder.addExtension(holder.getExtension(extensionOID));
        }
    }

    /** Custom mode: caller picks exactly which AuthorizationList tags get overridden. */
    private static Certificate[] createCertInternal(Certificate[] caList, boolean useKeyboxKeyInfo, Set<Integer> tags) {
        if (caList == null) throw new UnsupportedOperationException();
        try {
            X509Certificate leaf = toX509Certificate(caList[0]);
            if (leaf.getExtensionValue(OID.getId()) == null) return caList;

            byte[] leafBytes = leaf.getEncoded();
            ParsedAttestation parsed = parseAttestation(leaf, leafBytes);
            SigningMaterial signingMaterial = getSigningMaterial(parsed, useKeyboxKeyInfo);
            String signatureAlgorithm = resolveSignatureAlgorithm(
                    parsed.leaf.getSigAlgName(), signingMaterial.privateKey);
            ContentSigner signer = new JcaContentSignerBuilder(signatureAlgorithm).build(signingMaterial.privateKey);

            X509v3CertificateBuilder builder = new X509v3CertificateBuilder(
                    signingMaterial.issuerHolder.getSubject(),
                    parsed.holder.getSerialNumber(),
                    parsed.holder.getNotBefore(),
                    parsed.holder.getNotAfter(),
                    parsed.holder.getSubject(),
                    signingMaterial.subjectPublicKeyInfo);

            builder.addExtension(buildAttestationExtension(parsed, tags));
            copyNonAttestationExtensions(builder, parsed.holder);

            Certificate[] result = new Certificate[signingMaterial.certificates.size() + 1];
            result[0] = new JcaX509CertificateConverter().getCertificate(builder.build(signer));
            for (int i = 0; i < signingMaterial.certificates.size(); i++) {
                result[i + 1] = signingMaterial.certificates.get(i);
            }
            return result;
        } catch (Throwable t) {
            Log.e(TAG, "Failed to rebuild attestation certificate chain", t);
        }
        return caList;
    }

    /**
     * Custom mode with an explicit RootOfTrust override: {@code verifiedBootKeyHex}/
     * {@code verifiedBootHashHex} (32-byte hex, nullable) win over the system property / original
     * attestation value, but only take effect when {@link #TAG_ROOT_OF_TRUST} is in {@code tags}.
     * {@code attestationIdSerial} (nullable) only takes effect when
     * {@link #TAG_ATTESTATION_ID_SERIAL} is in {@code tags}.
     */
    private static Certificate[] createCertInternal(Certificate[] caList, boolean useKeyboxKeyInfo, Set<Integer> tags,
                                                      String verifiedBootKeyHex, String verifiedBootHashHex,
                                                      String attestationIdSerial) {
        if (caList == null) throw new UnsupportedOperationException();
        try {
            X509Certificate leaf = toX509Certificate(caList[0]);
            if (leaf.getExtensionValue(OID.getId()) == null) return caList;

            byte[] leafBytes = leaf.getEncoded();
            ParsedAttestation parsed = parseAttestation(leaf, leafBytes);
            SigningMaterial signingMaterial = getSigningMaterial(parsed, useKeyboxKeyInfo);
            String signatureAlgorithm = resolveSignatureAlgorithm(
                    parsed.leaf.getSigAlgName(), signingMaterial.privateKey);
            ContentSigner signer = new JcaContentSignerBuilder(signatureAlgorithm).build(signingMaterial.privateKey);

            X509v3CertificateBuilder builder = new X509v3CertificateBuilder(
                    signingMaterial.issuerHolder.getSubject(),
                    parsed.holder.getSerialNumber(),
                    parsed.holder.getNotBefore(),
                    parsed.holder.getNotAfter(),
                    parsed.holder.getSubject(),
                    signingMaterial.subjectPublicKeyInfo);

            builder.addExtension(buildAttestationExtension(parsed, tags, verifiedBootKeyHex, verifiedBootHashHex, attestationIdSerial));
            copyNonAttestationExtensions(builder, parsed.holder);

            Certificate[] result = new Certificate[signingMaterial.certificates.size() + 1];
            result[0] = new JcaX509CertificateConverter().getCertificate(builder.build(signer));
            for (int i = 0; i < signingMaterial.certificates.size(); i++) {
                result[i + 1] = signingMaterial.certificates.get(i);
            }
            return result;
        } catch (Throwable t) {
            Log.e(TAG, "Failed to rebuild attestation certificate chain", t);
        }
        return caList;
    }

    /** Custom mode: caller picks exactly which AuthorizationList tags get overridden. */
    private static byte[] modifyLeafInternal(byte[] bytes, boolean useKeyboxKeyInfo, Set<Integer> tags) throws Throwable {
        vslog("modifyLeaf");

        ParsedAttestation parsed = parseAttestation(bytes);
        algo = parsed.keyAlgorithm;

        SigningMaterial signingMaterial = getSigningMaterial(parsed, useKeyboxKeyInfo);
        String signatureAlgorithm = resolveSignatureAlgorithm(
                parsed.leaf.getSigAlgName(), signingMaterial.privateKey);
        ContentSigner signer = new JcaContentSignerBuilder(signatureAlgorithm).build(signingMaterial.privateKey);

        X509v3CertificateBuilder builder = new X509v3CertificateBuilder(
                signingMaterial.issuerHolder.getSubject(),
                parsed.holder.getSerialNumber(),
                parsed.holder.getNotBefore(),
                parsed.holder.getNotAfter(),
                parsed.holder.getSubject(),
                signingMaterial.subjectPublicKeyInfo);

        builder.addExtension(buildAttestationExtension(parsed, tags));
        copyNonAttestationExtensions(builder, parsed.holder);
        return builder.build(signer).getEncoded();
    }

    /**
     * Custom mode with an explicit RootOfTrust override; see the matching
     * {@code createCertInternal} overload for the exact semantics.
     */
    private static byte[] modifyLeafInternal(byte[] bytes, boolean useKeyboxKeyInfo, Set<Integer> tags,
                                              String verifiedBootKeyHex, String verifiedBootHashHex,
                                              String attestationIdSerial) throws Throwable {
        vslog("modifyLeaf");

        ParsedAttestation parsed = parseAttestation(bytes);
        algo = parsed.keyAlgorithm;

        SigningMaterial signingMaterial = getSigningMaterial(parsed, useKeyboxKeyInfo);
        String signatureAlgorithm = resolveSignatureAlgorithm(
                parsed.leaf.getSigAlgName(), signingMaterial.privateKey);
        ContentSigner signer = new JcaContentSignerBuilder(signatureAlgorithm).build(signingMaterial.privateKey);

        X509v3CertificateBuilder builder = new X509v3CertificateBuilder(
                signingMaterial.issuerHolder.getSubject(),
                parsed.holder.getSerialNumber(),
                parsed.holder.getNotBefore(),
                parsed.holder.getNotAfter(),
                parsed.holder.getSubject(),
                signingMaterial.subjectPublicKeyInfo);

        builder.addExtension(buildAttestationExtension(parsed, tags, verifiedBootKeyHex, verifiedBootHashHex, attestationIdSerial));
        copyNonAttestationExtensions(builder, parsed.holder);
        return builder.build(signer).getEncoded();
    }

    /** Stable baseline entry point: full patch (every TAG_* in ALL_ATTESTATION_TAGS). */
    public static Certificate[] CreateCert(Certificate[] caList, boolean useKeyboxKeyInfo) {
        return createCertInternal(caList, useKeyboxKeyInfo, ALL_ATTESTATION_TAGS);
    }

    /** Custom mode driven by an arbitrary tag set (see TAG_* constants); tags==null means "all". */
    public static Certificate[] CreateCert(Certificate[] caList, boolean useKeyboxKeyInfo, Set<Integer> tags) {
        return createCertInternal(caList, useKeyboxKeyInfo, tags);
    }

    /**
     * Custom mode with an explicit RootOfTrust override: {@code verifiedBootKeyHex}/
     * {@code verifiedBootHashHex} (32-byte hex, nullable) are only applied when
     * {@link #TAG_ROOT_OF_TRUST} is in {@code tags}. {@code attestationIdSerial} (nullable) is
     * only applied when {@link #TAG_ATTESTATION_ID_SERIAL} is in {@code tags}.
     */
    public static Certificate[] CreateCert(Certificate[] caList, boolean useKeyboxKeyInfo, Set<Integer> tags,
                                            String verifiedBootKeyHex, String verifiedBootHashHex,
                                            String attestationIdSerial) {
        return createCertInternal(caList, useKeyboxKeyInfo, tags, verifiedBootKeyHex, verifiedBootHashHex, attestationIdSerial);
    }

    /** Stable baseline entry point: full patch (every TAG_* in ALL_ATTESTATION_TAGS). */
    public static byte[] modifyLeaf(byte[] bytes, boolean useKeyboxKeyInfo) throws Throwable {
        return modifyLeafInternal(bytes, useKeyboxKeyInfo, ALL_ATTESTATION_TAGS);
    }

    /** Custom mode driven by an arbitrary tag set (see TAG_* constants); tags==null means "all". */
    public static byte[] modifyLeaf(byte[] bytes, boolean useKeyboxKeyInfo, Set<Integer> tags) throws Throwable {
        return modifyLeafInternal(bytes, useKeyboxKeyInfo, tags);
    }

    /**
     * Custom mode with an explicit RootOfTrust override: {@code verifiedBootKeyHex}/
     * {@code verifiedBootHashHex} (32-byte hex, nullable) are only applied when
     * {@link #TAG_ROOT_OF_TRUST} is in {@code tags}. {@code attestationIdSerial} (nullable) is
     * only applied when {@link #TAG_ATTESTATION_ID_SERIAL} is in {@code tags}.
     */
    public static byte[] modifyLeaf(byte[] bytes, boolean useKeyboxKeyInfo, Set<Integer> tags,
                                     String verifiedBootKeyHex, String verifiedBootHashHex,
                                     String attestationIdSerial) throws Throwable {
        return modifyLeafInternal(bytes, useKeyboxKeyInfo, tags, verifiedBootKeyHex, verifiedBootHashHex, attestationIdSerial);
    }

    public static Certificate[] CreateCert_Stock(Certificate[] caList) {
        return createCertInternal(caList, false, ALL_ATTESTATION_TAGS);
    }

    public static Certificate[] CreateCert_Keybox(Certificate[] caList) {
        return createCertInternal(caList, true, ALL_ATTESTATION_TAGS);
    }

    public static byte[] getCertificateChain(String algo) throws Throwable {
        LinkedList<Certificate> certificates;

        synchronized (KEYBOX_LOCK) {
            if (KeyProperties.KEY_ALGORITHM_EC.equals(algo)) {
                certificates = new LinkedList<>(EC_CERTS);
            } else if (KeyProperties.KEY_ALGORITHM_RSA.equals(algo)) {
                certificates = new LinkedList<>(RSA_CERTS);
            } else {
                throw new Exception("Unsupported algorithm: " + algo);
            }
        }

        ByteArrayOutputStream outputStream = new ByteArrayOutputStream();
        for (Certificate cert : certificates) {
            outputStream.write(cert.getEncoded());
        }

        return outputStream.toByteArray();
    }

    public static byte[] modifyLeaf_Stock(byte[] bytes) throws Throwable {
        return modifyLeafInternal(bytes, false, ALL_ATTESTATION_TAGS);
    }

    public static byte[] modifyLeaf_Keybox(byte[] bytes) throws Throwable {
        return modifyLeafInternal(bytes, true, ALL_ATTESTATION_TAGS);
    }

    public static String onGetKeyEntry(String keyEntryResponse) {
        return keyEntryResponse;
    }

    // ---------------------------------------------------------------------
    // Route B: build an attestation certificate chain FROM SCRATCH.
    //
    // The CreateCert/modifyLeaf path above re-signs a TEE-produced leaf that
    // already carries the attestation extension. On RKP-only devices whose
    // attestation key pool cannot be provisioned (keystore2 get_rkpd_
    // attestation_key() → HTTP error → KeyMint -74 ATTESTATION_KEYS_NOT_
    // PROVISIONED), key generation fails before any such leaf exists. The
    // framework generate hook then generates the key WITHOUT an attestation
    // challenge (which the TEE can always do) and calls generateChain() to
    // synthesize the whole attestation leaf and chain from the keybox.
    // ---------------------------------------------------------------------

    /** Android attestation / KeyMint schema (1.3.6.1.4.1.11129.2.1.17). */
    private static final int ATTESTATION_VERSION = 400;   // KeyMint 4.0 (Android 16 era)
    private static final int KEYMASTER_VERSION = 400;
    private static final int SECURITY_LEVEL_TEE = 1;      // TrustedEnvironment
    private static final int KM_ORIGIN_GENERATED = 0;

    /** AuthorizationList tag numbers used only by the from-scratch builder. */
    private static final int TAG_PURPOSE = 1;
    private static final int TAG_ALGORITHM = 2;
    private static final int TAG_KEY_SIZE = 3;
    private static final int TAG_DIGEST = 5;
    private static final int TAG_EC_CURVE = 10;
    private static final int TAG_RSA_PUBLIC_EXPONENT = 200;
    private static final int TAG_NO_AUTH_REQUIRED = 503;
    private static final int TAG_CREATION_DATETIME = 701;
    private static final int TAG_ORIGIN = 702;
    private static final int TAG_ATTESTATION_APPLICATION_ID = 709;

    /** KeyMint algorithm enum. */
    public static final int KM_ALGORITHM_RSA = 1;
    public static final int KM_ALGORITHM_EC = 3;

    private static DERTaggedObject tagged(int tag, ASN1Encodable value) {
        return new DERTaggedObject(true, tag, value);
    }

    private static DERSet intSet(int[] values) {
        ASN1EncodableVector v = new ASN1EncodableVector();
        if (values != null) {
            for (int value : values) {
                v.add(new ASN1Integer(value));
            }
        }
        return new DERSet(v);
    }

    /**
     * AttestationApplicationId ::= SEQUENCE {
     *   package_infos     SET OF AttestationPackageInfo,
     *   signature_digests SET OF OCTET_STRING }
     * AttestationPackageInfo ::= SEQUENCE { package_name OCTET_STRING, version INTEGER }
     *
     * Play Integrity / GMS match this against the calling app, so it must carry
     * the real package name and the SHA-256 of its signing certificate.
     */
    private static byte[] buildAttestationApplicationId(String packageName, long versionCode,
                                                        byte[] signatureDigest) throws Exception {
        ASN1EncodableVector packageInfo = new ASN1EncodableVector();
        packageInfo.add(new DEROctetString(
                (packageName == null ? "" : packageName).getBytes(StandardCharsets.UTF_8)));
        packageInfo.add(new ASN1Integer(versionCode));

        DERSet packageInfos = new DERSet(new DERSequence(packageInfo));
        DERSet signatureDigests = new DERSet(
                new DEROctetString(signatureDigest == null ? new byte[0] : signatureDigest));

        ASN1EncodableVector aaid = new ASN1EncodableVector();
        aaid.add(packageInfos);
        aaid.add(signatureDigests);
        return new DERSequence(aaid).getEncoded();
    }

    /**
     * Builds a complete, keybox-signed attestation chain for a freshly generated key that the
     * TEE could not attest. Every value comes from the caller's KeyGenParameterSpec (translated
     * to KeyMint enums by the framework hook) so the result echoes the app's own challenge,
     * purposes and digests.
     *
     * @param keymasterAlgo {@link #KM_ALGORITHM_EC} or {@link #KM_ALGORITHM_RSA}.
     * @param ecCurve       KeyMint EcCurve enum (P-224=0, P-256=1, P-384=2, P-521=3), or -1 for RSA.
     * @param rsaExponent   big-endian RSA public exponent bytes, or null for EC.
     * @return leaf + keybox chain, or null on any failure (caller then propagates the original error).
     */
    public static Certificate[] generateChain(PublicKey publicKey, byte[] challenge,
                                              int keymasterAlgo, int keySize, int[] purposes, int[] digests,
                                              int ecCurve, byte[] rsaExponent,
                                              String packageName, long versionCode, byte[] signatureDigest) {
        if (publicKey == null) return null;
        try {
            boolean isEc = keymasterAlgo == KM_ALGORITHM_EC;
            // The getKeyEntry hook calls getCertificateChain(algo) right after this
            // to append the keybox issuer chain, so publish the selected algorithm.
            algo = isEc ? KeyProperties.KEY_ALGORITHM_EC : KeyProperties.KEY_ALGORITHM_RSA;

            List<Certificate> keyboxCerts;
            X509CertificateHolder issuerHolder;
            PrivateKey signingKey;
            synchronized (KEYBOX_LOCK) {
                keyboxCerts = new ArrayList<>(isEc ? EC_CERTS : RSA_CERTS);
                issuerHolder = isEc ? EC_holder : RSA_holder;
                signingKey = isEc ? EC_P : RSA_P;
            }
            if (keyboxCerts.isEmpty() || issuerHolder == null || signingKey == null) {
                vslog("generateChain: no keybox signing material for "
                        + (isEc ? "EC" : "RSA"));
                return null;
            }

            // softwareEnforced: creationDateTime (701), attestationApplicationId (709).
            ASN1EncodableVector sw = new ASN1EncodableVector();
            sw.add(tagged(TAG_CREATION_DATETIME, new ASN1Integer(System.currentTimeMillis())));
            sw.add(tagged(TAG_ATTESTATION_APPLICATION_ID, new DEROctetString(
                    buildAttestationApplicationId(packageName, versionCode, signatureDigest))));
            DERSequence softwareEnforced = new DERSequence(sw);

            // teeEnforced in ascending tag order.
            ASN1EncodableVector tee = new ASN1EncodableVector();
            tee.add(tagged(TAG_PURPOSE, intSet(purposes)));
            tee.add(tagged(TAG_ALGORITHM, new ASN1Integer(keymasterAlgo)));
            tee.add(tagged(TAG_KEY_SIZE, new ASN1Integer(keySize)));
            tee.add(tagged(TAG_DIGEST, intSet(digests)));
            if (isEc && ecCurve >= 0) {
                tee.add(tagged(TAG_EC_CURVE, new ASN1Integer(ecCurve)));
            }
            if (!isEc && rsaExponent != null && rsaExponent.length > 0) {
                tee.add(tagged(TAG_RSA_PUBLIC_EXPONENT, new ASN1Integer(new BigInteger(1, rsaExponent))));
            }
            tee.add(tagged(TAG_NO_AUTH_REQUIRED, DERNull.INSTANCE));
            tee.add(tagged(TAG_ORIGIN, new ASN1Integer(KM_ORIGIN_GENERATED)));
            tee.add(tagged(TAG_ROOT_OF_TRUST, buildRootOfTrust(
                    resolveVerifiedBootKey(null, null),
                    resolveVerifiedBootHash(null, null),
                    true,
                    VERIFIED_BOOT_VERIFIED)));
            tee.add(tagged(TAG_OS_VERSION, new ASN1Integer(getOsVersionLevelVal())));
            tee.add(tagged(TAG_OS_PATCH_LEVEL, new ASN1Integer(getOsPatchLevelVal())));
            tee.add(tagged(TAG_VENDOR_PATCH_LEVEL, new ASN1Integer(getVendorBootPatchLevelVal())));
            tee.add(tagged(TAG_BOOT_PATCH_LEVEL, new ASN1Integer(getVendorBootPatchLevelVal())));
            DERSequence teeEnforced = new DERSequence(tee);

            // KeyDescription SEQUENCE.
            ASN1EncodableVector kd = new ASN1EncodableVector();
            kd.add(new ASN1Integer(ATTESTATION_VERSION));
            kd.add(new ASN1Enumerated(SECURITY_LEVEL_TEE));
            kd.add(new ASN1Integer(KEYMASTER_VERSION));
            kd.add(new ASN1Enumerated(SECURITY_LEVEL_TEE));
            kd.add(new DEROctetString(challenge == null ? new byte[0] : challenge));
            kd.add(new DEROctetString(new byte[0])); // uniqueId
            kd.add(softwareEnforced);
            kd.add(teeEnforced);
            Extension attestationExtension = new Extension(
                    OID, false, new DEROctetString(new DERSequence(kd)));

            SubjectPublicKeyInfo spki = SubjectPublicKeyInfo.getInstance(publicKey.getEncoded());
            String signatureAlgorithm = resolveSignatureAlgorithm(null, signingKey);
            ContentSigner signer = new JcaContentSignerBuilder(signatureAlgorithm).build(signingKey);

            X509v3CertificateBuilder builder = new X509v3CertificateBuilder(
                    issuerHolder.getSubject(),
                    new BigInteger(64, SECURE_RANDOM),
                    new Date(System.currentTimeMillis() - 3600_000L),
                    issuerHolder.getNotAfter(),
                    new X500Name("CN=Android Keystore Key"),
                    spki);
            builder.addExtension(Extension.keyUsage, true, new KeyUsage(KeyUsage.digitalSignature));
            builder.addExtension(attestationExtension);

            Certificate[] result = new Certificate[keyboxCerts.size() + 1];
            result[0] = new JcaX509CertificateConverter().getCertificate(builder.build(signer));
            for (int i = 0; i < keyboxCerts.size(); i++) {
                result[i + 1] = keyboxCerts.get(i);
            }
            vslog("generateChain: synthesized " + result.length + "-cert chain for "
                    + packageName + " (" + (isEc ? "EC" : "RSA") + ")");
            return result;
        } catch (Throwable t) {
            Log.e(TAG, "generateChain failed", t);
            return null;
        }
    }
}
